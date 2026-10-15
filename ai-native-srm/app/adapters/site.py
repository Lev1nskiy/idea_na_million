"""Адаптер контракта сайта-фронтенда («СИНАПС») к моделям бэкенда.

Зачем: сайт коллеги уже написан и имеет собственный REST-контракт (camelCase, строковые
id вида `dlg-0001`/`C-1001`, обёртки `{conversations: [...]}`, Policy в форме с `work_hours.from/to`).
Мы не переписываем UI и не меняем собственный API (Swagger/Streamlit/Telegram им пользуются),
поэтому сайт обслуживает отдельная поверхность `/api/site/*`, а этот модуль — единственное место
перевода «их формы ↔ наши таблицы». Правило: бизнес-логики здесь нет, только маппинг типов.

Соответствия, которые важно держать в голове:
* `clientId` сайта ↔ `clients.id`: `C-1001` = клиент 1 (обратный маппинг — `parse_id`);
* сегмент клиента (std/corp/new/loyal) — канон демо-данных, а движковый `status`
  (new/regular/corporate/loyal) влияет на лояльную скидку: см. `status_from_segment`;
* статус диалога `LOST` (клиент ушёл, ставит канал/менеджер) в контракте сайта отсутствует —
  он отдаётся как `RESOLVED`, чтобы UI не показывал неизвестное значение.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Optional

from core.schemas import utcnow

from core import tools
from core.policy import get_manager_sla
from core.schemas import PipelineTrace, Policy

# ---------------------------------------------------------------------------
# Идентификаторы: int в БД ↔ строки контракта сайта
# ---------------------------------------------------------------------------
CLIENT_BASE = 1000  # запасной вариант нумерации, если в данных нет канонического site_id

SEGMENT_TO_STATUS = {"new": "new", "std": "regular", "corp": "corporate", "loyal": "loyal"}
STATUS_TO_SEGMENT = {"new": "new", "regular": "std", "corporate": "corp", "loyal": "loyal", "vip": "loyal"}

ORDER_STATUS = {"delivered": "delivered", "overdue": "overdue", "in_transit": "shipping",
                "processing": "shipping", "shipping": "shipping"}


def client_id_out(client_id: int) -> str:
    """`clients.id` → идентификатор сайта (`C-1001`).

    Канон идентификаторов принадлежит сайту и лежит в демо-данных (`site_id` у клиента):
    их справочник и тексты сценариев знают `C-1001` = «ГрандСнаб». Если записи нет,
    работает арифметика по умолчанию — так же, как для клиентов, созданных из чата.
    """
    source = tools.get_client(client_id)
    if source and source.get("site_id"):
        return str(source["site_id"])
    return f"C-{CLIENT_BASE + int(client_id)}"


def client_pk_of(site_id: str) -> int:
    """Обратный маппинг: `C-1001` → `clients.id` (2). Неизвестный id — арифметика по умолчанию."""
    for client_id, source in tools._clients().items():  # noqa: SLF001 — справочник как есть
        if source.get("site_id") == site_id:
            return client_id
    digits = re.findall(r"\d+", site_id)
    if not digits:
        raise ValueError(f"не разобрать идентификатор клиента: {site_id!r}")
    value = int(digits[0])
    return value - CLIENT_BASE if value > CLIENT_BASE else value


def parse_id(value: str | int | None, prefix: str = "") -> int:
    """`dlg-0007`/`C-1005`/`7` → 7. Бросает ValueError на мусоре (роутер превращает в 404)."""
    if value is None:
        raise ValueError("пустой идентификатор")
    text = str(value).strip()
    if prefix and text.startswith(prefix):
        text = text[len(prefix):]
    digits = re.findall(r"\d+", text)
    if not digits:
        raise ValueError(f"не разобрать идентификатор: {value!r}")
    return int(digits[0])


def out_id(prefix: str, row_id: int) -> str:
    """7 → `dlg-0007` (как в контракте сайта)."""
    return f"{prefix}-{int(row_id):04d}"


def segment_from_status(status: str | None) -> str:
    return STATUS_TO_SEGMENT.get((status or "new").lower(), "new")


def status_from_segment(segment: str | None) -> str:
    return SEGMENT_TO_STATUS.get((segment or "new").lower(), "new")


# ---------------------------------------------------------------------------
# Время: naive UTC в БД → ISO-8601 с Z (как ждёт UI сайта)
# ---------------------------------------------------------------------------
def iso(ts: datetime | None) -> str:
    return (ts or utcnow()).isoformat() + "Z"


def waiting_minutes(ts: datetime | None) -> int:
    if ts is None:
        return 0
    return max(0, int((utcnow() - ts).total_seconds() // 60))


# ---------------------------------------------------------------------------
# Клиенты и заказы
# ---------------------------------------------------------------------------
def order_records(order: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Заказ бэкенда (может содержать несколько позиций) → записи заказов сайта (по одной на позицию)."""
    rows: list[dict[str, Any]] = []
    for line in order.get("items", []):
        serials = list(line.get("serials") or [])
        rows.append(
            {
                "id": order["order_id"],
                "sku": line["sku"],
                "product": catalog.get(line["sku"], {}).get("name", line["sku"]),
                "serial": serials[0] if serials else None,
                "purchasedAt": order.get("date"),
                "status": ORDER_STATUS.get(str(order.get("status", "")).lower(), "shipping"),
                "sum": int(line.get("price", 0)) * int(line.get("qty", 1)),
                "etaDaysLate": order.get("delay_days") or None,
            }
        )
    return rows


def client_record(client_id: int) -> dict[str, Any] | None:
    """История клиента из демо-данных (`data/clients_history.json`) в форме `ClientRecord` сайта."""
    source = tools.get_client(client_id)
    if not source:
        return None
    catalog = tools._catalog()  # noqa: SLF001 — справочник читается как есть, без копии
    orders: list[dict[str, Any]] = []
    for order in source.get("orders", []):
        orders.extend(order_records(order, catalog))
    return {
        "id": client_id_out(client_id),
        "name": source["name"],
        "contact": source.get("contact") or "",
        "phone": source.get("phone") or "",
        "email": source.get("email") or "",
        "segment": source.get("segment") or segment_from_status(source.get("status")),
        "note": source.get("internal_notes") or "",
        "orders": orders,
    }


def client_option(row: Any, source: dict[str, Any] | None) -> dict[str, Any]:
    """Строка селектора клиентов для чата (замена статического `CLIENT_OPTIONS` на сайте)."""
    return {
        "id": client_id_out(row.id),
        "name": row.name,
        "contact": (source or {}).get("contact") or "",
        "segment": segment_from_status(row.status),
        "company": row.company,
        "status": row.status,
    }


def order_status_ru(status: str) -> str:
    return {"delivered": "доставлен", "overdue": "просрочен", "shipping": "в пути"}.get(status, status)


# ---------------------------------------------------------------------------
# Диалоги, сообщения, решения, сделки
# ---------------------------------------------------------------------------
def conversation_out(row: Any, *, last_message: str | None, last_at: datetime | None,
                     message_count: int) -> dict[str, Any]:
    """Строка списка диалогов в форме сайта (плоский `Conversation` + поля-хелперы)."""
    status = "RESOLVED" if row.status == "LOST" else row.status
    return {
        "id": out_id("dlg", row.id),
        "clientId": client_id_out(row.client_id),
        "channel": row.channel,
        "status": status,
        "suspicious": bool(row.suspicious),
        "discountOfferMade": bool(row.discount_offer_made),
        "discountPushCount": row.discount_push_count or 0,
        "lowConfStreak": row.low_conf_streak or 0,
        "category": row.category,
        "lastAiPingAt": iso(row.last_ai_ping_ts) if row.last_ai_ping_ts else None,
        "createdAt": iso(row.created_at),
        "updatedAt": iso(row.updated_at),
        "lastMessage": last_message or "",
        "lastAt": iso(last_at or row.updated_at),
        "messageCount": message_count,
        "waitingMinutes": waiting_minutes(row.created_at) if status == "HOLD" else 0,
    }


def message_out(row: Any, conversation_ext_id: str, category: str | None = None) -> dict[str, Any]:
    return {
        "id": out_id("m", row.id),
        "convId": conversation_ext_id,
        "role": row.role,
        "text": row.text,
        "phase": row.phase,
        "deterministic": bool(row.deterministic),
        "latencyMs": row.latency_ms,
        "category": category,
        "at": iso(row.ts),
    }


def decision_out(row: Any) -> dict[str, Any]:
    return {
        "id": out_id("dec", row.id),
        "convId": out_id("dlg", row.conversation_id),
        "category": row.category,
        "confidence": round(float(row.confidence or 0.0), 3),
        "policyVersion": row.policy_version,
        "overriddenBy": row.overridden_by,
        "overrideReason": row.override_reason,
        "createdAt": iso(row.ts),
    }


def handoff_out(row: Any) -> dict[str, Any]:
    summary = row.summary_json or {}
    status = {"PENDING": "pending", "TAKEN": "taken", "CLOSED": "resolved"}.get(row.status, "pending")
    return {
        "id": out_id("ho", row.id),
        "convId": out_id("dlg", row.conversation_id),
        "status": status,
        "takenBy": row.taken_by,
        "createdAt": iso(row.created_at),
        "summary": {
            "essence": summary.get("dispute_essence", ""),
            "demands": list(summary.get("client_demands") or []),
            "historyDigest": list(summary.get("history_digest") or []),
            "remedyOptions": list(summary.get("remedy_options_mentioned") or []),
            "recommendedRange": summary.get("recommended_range", ""),
            "sentiment": summary.get("sentiment", 0.0),
            "urgency": summary.get("urgency", "medium"),
        },
    }


def deal_out(row: Any) -> dict[str, Any]:
    return {
        "id": out_id("deal", row.id),
        "clientId": client_id_out(row.client_id),
        "convId": out_id("dlg", row.conversation_id) if row.conversation_id else "",
        "product": row.product,
        "qty": row.qty,
        "budget": row.budget,
        "discountPct": row.discount_pct,
        "sum": row.budget,
        "stage": row.stage,
        "note": row.notes or "",
        "updatedAt": iso(row.updated_at),
    }


def notification_out(row: Any, *, client_name: str = "—", conv_status: str | None = None,
                     conversation_ext_id: str | None = None) -> dict[str, Any]:
    """Уведомление менеджеру в форме сайта; `level` — уровень эскалации (совпадает с reminder_level)."""
    level = int(row.reminder_level or 1)
    return {
        "id": out_id("ntf", row.id),
        "convId": conversation_ext_id or (out_id("dlg", row.conversation_id) if row.conversation_id else ""),
        "type": row.type,
        "text": row.text,
        "read": bool(row.read),
        "active": bool(row.active),
        "level": level,
        "telegram": level >= 2,
        "slaBreached": level >= 4,
        "createdAt": iso(row.created_at),
        "lastNotifiedAt": iso(row.last_notified_at),
        "clientName": client_name,
        "convStatus": conv_status,
        "waitingMinutes": waiting_minutes(row.created_at),
    }


def incident_out(row: Any, *, client_name: str = "—") -> dict[str, Any]:
    patterns = [p for p in str(row.pattern or "").split(",") if p]
    return {
        "id": out_id("sec", row.id),
        "convId": out_id("dlg", row.conversation_id) if row.conversation_id else "",
        "patterns": patterns,
        "rawTextMasked": row.raw_text_masked,
        "textHash": (row.text_hash or "")[:12],
        "neutralized": True,  # инцидент фиксируется только когда инъекция уже нейтрализована шаблоном
        "at": iso(row.ts),
        "clientName": client_name,
    }


def event_out(row: Any, *, category: str | None = None) -> dict[str, Any]:
    payload = row.payload if isinstance(row.payload, dict) else {}
    return {
        "id": out_id("ev", row.id),
        "type": row.type,
        "payload": _payload_text(row.type, payload),
        "latencyMs": row.latency_ms,
        "category": category,
        "convId": out_id("dlg", row.conversation_id) if row.conversation_id else None,
        "at": iso(row.ts),
    }


def _payload_text(event_type: str, payload: dict[str, Any]) -> str:
    """Короткая человекочитаемая строка события (UI показывает её в журнале LEARNING)."""
    if event_type in ("validator_block", "validator_bypass"):
        codes = ", ".join(payload.get("codes", []) or [])
        return f"{payload.get('scenario', 'ответ')}: {codes}"
    if event_type == "first_response":
        return f"ответ · {payload.get('phase', 'FINAL')} · {payload.get('group', 'other')}"
    if event_type == "decision":
        return f"{payload.get('category')} · {payload.get('route')} · Policy v{payload.get('policy_version')}"
    if event_type == "message_received":
        return f"входящее ({payload.get('channel', 'web')})"
    if event_type == "dispute_hold":
        return "спор: ACK → HOLD" + (f" · поймал {payload['caught_by']}" if payload.get("caught_by") else "")
    if event_type == "injection_detected":
        return "инъекция нейтрализована · " + ", ".join(payload.get("patterns", []) or [])
    parts = [f"{k}={v}" for k, v in list(payload.items())[:3]]
    return " ".join(parts) or event_type


# ---------------------------------------------------------------------------
# Policy Matrix
# ---------------------------------------------------------------------------
def _hhmm(hour: int) -> str:
    return f"{int(hour):02d}:00"


def policy_out(policy: Policy, *, author: str, updated_at: datetime | None) -> dict[str, Any]:
    """Policy бэкенда → Policy сайта (рабочие часы переводим из часов+UTC-смещения в «09:00–18:00»)."""
    wh = policy.manager_sla.work_hours
    return {
        "version": policy.version,
        "author": author,
        "updatedAt": iso(updated_at),
        "discounts": {
            "max_pct": policy.discounts.max_pct,
            "tiers": [{"min_sum": t.min_sum, "pct": t.pct} for t in policy.discounts.tiers],
            "bundle_max_pct": policy.discounts.bundle_max_pct,
            "loyal_bonus_pct": policy.discounts.loyal_bonus_pct,
        },
        "warranty": {"replace_hours": policy.warranty.replace_hours,
                     "requires_confirmed_order": policy.warranty.requires_confirmed_order},
        "delivery_compensation": [{"delay_days_gt": r.delay_days_gt, "pct_of_sum": r.pct_of_sum}
                                  for r in policy.delivery_compensation],
        "remedy_options": list(policy.remedy_options),
        "manager_sla": {
            "response_hours": policy.manager_sla.response_hours,
            "off_hours_response_hours": policy.manager_sla.off_hours_response_hours,
            "work_hours": {"from": _hhmm(wh.start_hour), "to": _hhmm(wh.end_hour), "days": list(wh.days)},
        },
        "thresholds": {
            "min_confidence": policy.thresholds.min_confidence,
            "discount_push_limit": policy.thresholds.discount_push_limit,
            "negative_sentiment": policy.thresholds.negative_sentiment,
            "hold_ping_minutes": policy.thresholds.hold_ping_minutes,
            "reminder_minutes": policy.thresholds.reminder_minutes,
        },
    }


def policy_in(body: dict[str, Any]) -> dict[str, Any]:
    """Policy сайта → частичный патч для `core.policy.update_policy` (обратно к нашей форме).

    Нужен только для полей, форма которых у нас отличается (рабочие часы, лояльные статусы);
    всё остальное совпадает по именам и уходит как есть.
    """
    patch: dict[str, Any] = {}
    for key in ("discounts", "warranty", "delivery_compensation", "remedy_options", "thresholds"):
        if key in body:
            patch[key] = body[key]
    if "discounts" in patch and isinstance(patch["discounts"], dict):
        patch["discounts"].pop("loyal_statuses", None)  # у нас этот список фиксирован
    sla = body.get("manager_sla")
    if isinstance(sla, dict):
        patch["manager_sla"] = {k: v for k, v in sla.items() if k != "work_hours"}
        work = sla.get("work_hours")
        if isinstance(work, dict):
            start, end = _parse_hhmm(work.get("from")) or 9, _parse_hhmm(work.get("to")) or 18
            patch["manager_sla"]["work_hours"] = {
                "start_hour": start,
                "end_hour": end,
                "days": list(work.get("days") or [1, 2, 3, 4, 5]),
            }
    return patch


def _parse_hhmm(value: Any) -> Optional[int]:
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{1,2})", value.strip())
    return int(match.group(1)) if match else None


def sla_out(policy: Policy) -> dict[str, Any]:
    """«Честный срок» связи с менеджером для симулятора сайта."""
    info = get_manager_sla(policy)
    hours = info.response_hours
    label = (
        f"≈{hours} ч (рабочее время)"
        if info.is_work_time
        else f"≈{hours} ч, в начале рабочего дня (сейчас нерабочее время)"
    )
    return {"isWorkTime": info.is_work_time, "hours": hours, "label": label}


# ---------------------------------------------------------------------------
# Трассировка пайплайна
# ---------------------------------------------------------------------------
def trace_out(trace: PipelineTrace | None) -> dict[str, Any] | None:
    """Трассировка ответа бэкенда → `PipelineTrace` сайта (панель на странице чата).

    Без решения панель показывать нечего (например, ответ в режиме HOLD или технический сбой):
    возвращаем None — сайт в этом случае рисует подсказку вместо разбора.
    """
    if trace is None or trace.decision is None:
        return None
    pre = trace.pre
    und = trace.und
    decision = trace.decision
    return {
        "pre": {
            "risk": pre.risk if pre else "NONE",
            "markers": list(pre.markers) if pre else [],
            "legit_text": pre.legit_text if pre else None,
            "discount_request_pct": pre.discount_request_pct if pre else None,
            "via": "lexicon" if (pre and pre.markers) else "none",
        },
        "und": {
            # Детерминированные пути (спор/инъекция) идут без LLM: показываем то, что известно.
            "intents": list(und.category_candidates) if und else [decision.category],
            "entities": _entities_out(und),
            "sentiment": float(und.sentiment) if und else 0.0,
            "confidence": float(und.confidence) if und else 1.0,
            "injection_suspected": bool(und.injection_suspected) if und else (pre.risk == "F" if pre else False),
            "language": und.language if und else "ru",
        },
        "decision": {
            "category": decision.category,
            "route": decision.route,
            "reasons": list(decision.reasons),
            "policy_version": decision.policy_version,
            "needs_manager_override": bool(decision.needs_manager_override),
            "clarify": bool(decision.clarify),
        },
        "guardrails": [{"id": g.id, "ok": g.ok, "detail": g.detail} for g in trace.guardrails],
        "whitelistSize": trace.whitelist_size,
    }


def _entities_out(und: Any) -> dict[str, Any]:
    if und is None:
        return {}
    e = und.entities
    out: dict[str, Any] = {}
    if e.product or e.sku:
        out["product"] = e.product or e.sku
    if e.qty is not None:
        out["qty"] = e.qty
    if e.budget is not None:
        out["budget"] = e.budget
    if e.discount_pct is not None:
        out["discount_pct"] = e.discount_pct
    if e.serial:
        out["serial"] = e.serial
    if e.people_count is not None:
        out["people"] = e.people_count
    return out


def first(items: Iterable[Any]) -> Any | None:
    """Первое значение итератора или None (хелпер для роутера)."""
    return next(iter(items), None)
