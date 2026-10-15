"""Контракты между слоями пайплайна (Pydantic v2).

Меняются только осознанно: на эти модели опираются все остальные модули.
Время везде — naive UTC (см. utcnow()).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Общие типы и утилиты
# ---------------------------------------------------------------------------
Category = Literal["A", "B", "C", "D", "E", "F", "G", "H", "I"]
Route = Literal["AUTONOMOUS", "SMOOTH_HOLD", "INJECTION"]
Phase = Literal["ACK", "INTERIM", "FINAL", "HOLD"]
Channel = Literal["web", "telegram", "email"]
RiskLevel = Literal["NONE", "I", "F"]

# Приоритет категорий при нескольких интентах: F > I > G/H > D > E > C > B > A
# (меньше число — выше приоритет).
CATEGORY_RANK: dict[str, int] = {
    "F": 0, "I": 1, "G": 2, "H": 2, "D": 3, "E": 4, "C": 5, "B": 6, "A": 7,
}


def utcnow() -> datetime:
    """Текущее время в UTC без tzinfo (так хранится в SQLite)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def top_category(*categories: str | None) -> str | None:
    """Возвращает наиболее приоритетную категорию из переданных."""
    valid = [c for c in categories if c in CATEGORY_RANK]
    return min(valid, key=lambda c: (CATEGORY_RANK[c], c)) if valid else None


# ---------------------------------------------------------------------------
# PERCEPTION / UNDERSTANDING / DECISION
# ---------------------------------------------------------------------------
class IncomingMessage(BaseModel):
    """Входящее сообщение клиента из любого канала."""

    conversation_id: int | None = None
    client_id: int
    channel: Channel = "web"
    text: str
    ts: datetime = Field(default_factory=utcnow)


class PreClassification(BaseModel):
    """Результат лексического pre-classifier (без LLM, < 100 мс)."""

    risk: RiskLevel = "NONE"
    markers: list[str] = Field(default_factory=list)
    legit_text: str | None = None  # текст без инъекционных фрагментов
    discount_request_pct: float | None = None


class Entities(BaseModel):
    """Сущности, извлечённые из сообщения."""

    product: str | None = None
    sku: str | None = None
    qty: int | None = None
    people_count: int | None = None
    budget: float | None = None
    discount_pct: float | None = None
    serial: str | None = None
    order_id: str | None = None


class Understanding(BaseModel):
    """Результат слоя UNDERSTANDING (LLM -> структурный JSON)."""

    intent: str = "unknown"
    category_candidates: list[Category] = Field(default_factory=list)
    entities: Entities = Field(default_factory=Entities)
    sentiment: float = Field(default=0.0, ge=-1.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    injection_suspected: bool = False
    language: str = "ru"


class Decision(BaseModel):
    """Результат слоя DECISION."""

    category: Category
    route: Route
    reasons: list[str] = Field(default_factory=list)
    policy_version: int = 0
    needs_manager_override: bool = False
    clarify: bool = False  # confidence < min_confidence -> уточняющий вопрос
    clarify_kind: Literal["generic", "people_count", "order_details", "hold_off"] | None = None
    # Граница C/I (Ст. 6): что делать со скидкой и какой процент (макс. по политике или выдаваемый)
    discount_action: Literal["grant", "offer_max", "confirm_limit", "none_available"] | None = None
    discount_pct: float | None = None
    # Изменения состояния диалога, которые применяет оркестратор (decide() сам ничего не пишет)
    state_updates: dict[str, Any] = Field(default_factory=dict)


class ConvState(BaseModel):
    """Снимок состояния диалога, нужный слою DECISION (decide() — чистая функция)."""

    status: str = "ACTIVE"  # ACTIVE | HOLD | HANDOFF | RESOLVED
    suspicious: bool = False
    discount_offer_made: bool = False
    discount_push_count: int = 0
    low_conf_streak: int = 0
    category: str | None = None


# ---------------------------------------------------------------------------
# Policy Matrix v5 (версионируется, хранится в БД)
# ---------------------------------------------------------------------------
class DiscountTier(BaseModel):
    """Порог скидки: заказ СВЫШЕ min_sum (строго больше) -> pct %."""

    min_sum: float = Field(ge=0)
    pct: float = Field(ge=0, le=100)


class DiscountsPolicy(BaseModel):
    max_pct: float = Field(default=15, ge=0, le=100)  # глобальный потолок
    tiers: list[DiscountTier] = Field(default_factory=list)
    bundle_max_pct: float = Field(default=15, ge=0, le=100)  # скидка при комплекте
    loyal_bonus_pct: float = Field(default=0, ge=0, le=100)
    loyal_statuses: list[str] = Field(default_factory=lambda: ["loyal", "vip"])


class WarrantyPolicy(BaseModel):
    replace_hours: int = Field(default=24, ge=0)
    requires_confirmed_order: bool = True


class DeliveryCompensationRule(BaseModel):
    """Задержка СТРОГО больше delay_days_gt дней -> pct_of_sum % от суммы."""

    delay_days_gt: int = Field(ge=0)
    pct_of_sum: float = Field(ge=0, le=100)


class WorkHours(BaseModel):
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=18, ge=1, le=24)
    days: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])  # isoweekday
    utc_offset_hours: int = 3


class ManagerSla(BaseModel):
    """«Честный срок» связи с менеджером (Ст. 6, 8)."""

    response_hours: int = Field(default=2, ge=0)
    off_hours_response_hours: int = Field(default=12, ge=0)
    work_hours: WorkHours = Field(default_factory=WorkHours)


class Thresholds(BaseModel):
    min_confidence: float = Field(default=0.7, ge=0, le=1)
    discount_push_limit: int = Field(default=2, ge=1)
    negative_sentiment: float = Field(default=-0.5, ge=-1, le=0)
    hold_ping_minutes: int = Field(default=10, ge=1)
    reminder_minutes: int = Field(default=15, ge=1)


class Policy(BaseModel):
    """Policy Matrix: версионируемый набор бизнес-правил."""

    version: int = 0  # проставляется из БД при чтении
    discounts: DiscountsPolicy = Field(default_factory=DiscountsPolicy)
    warranty: WarrantyPolicy = Field(default_factory=WarrantyPolicy)
    delivery_compensation: list[DeliveryCompensationRule] = Field(default_factory=list)
    remedy_options: list[str] = Field(default_factory=list)
    manager_sla: ManagerSla = Field(default_factory=ManagerSla)
    thresholds: Thresholds = Field(default_factory=Thresholds)


class SlaInfo(BaseModel):
    """Актуальный честный срок ответа менеджера на конкретный момент."""

    is_work_time: bool
    response_hours: int
    start_hour: int
    end_hour: int
    days: list[int]


class Compensation(BaseModel):
    """Компенсация за задержку поставки (из Policy)."""

    pct: float
    amount: float


# ---------------------------------------------------------------------------
# ACTION / HANDOFF
# ---------------------------------------------------------------------------
class ToolFacts(BaseModel):
    """Факты из tools/Policy — единственный источник чисел для ответа."""

    prices: dict[str, float] = Field(default_factory=dict)
    discount: dict[str, Any] = Field(default_factory=dict)
    lead_time_days: int | None = None
    warranty: dict[str, Any] = Field(default_factory=dict)
    compensation: dict[str, Any] = Field(default_factory=dict)
    sla: dict[str, Any] = Field(default_factory=dict)
    quote: dict[str, Any] = Field(default_factory=dict)
    number_whitelist: set[str] = Field(default_factory=set)


class BotMessage(BaseModel):
    text: str
    phase: Phase = "FINAL"
    deterministic: bool = False  # True — шаблон без LLM


class GuardrailNote(BaseModel):
    """Итог одной проверки Устава при подготовке текста клиенту (для трассировки пайплайна)."""

    id: str  # V1…V7
    ok: bool = False
    detail: str = ""


class PipelineTrace(BaseModel):
    """Снимок пайплайна PERCEPTION → ACTION: что увидел pre-classifier, как поняли и решили,
    какие валидаторы сработали. Отдаётся в ответе API (сайт-фронтенд показывает это менеджеру),
    в БД не сохраняется — источник истины остаётся в decisions/events."""

    pre: PreClassification | None = None
    und: Understanding | None = None
    decision: Decision | None = None
    guardrails: list[GuardrailNote] = Field(default_factory=list)
    whitelist_size: int = 0


class BotResponse(BaseModel):
    conversation_id: int | None = None
    messages: list[BotMessage] = Field(default_factory=list)
    card_updates: dict[str, Any] = Field(default_factory=dict)
    notifications: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    trace: PipelineTrace | None = None


class HandoffSummary(BaseModel):
    """Саммари для менеджера: читается за ≤ 30 секунд (Ст. 8)."""

    dispute_essence: str = ""
    client_demands: list[str] = Field(default_factory=list)
    history_digest: list[str] = Field(default_factory=list)
    remedy_options_mentioned: list[str] = Field(default_factory=list)
    recommended_range: str = ""
    sentiment: float = 0.0
    urgency: Literal["low", "medium", "high"] = "medium"
