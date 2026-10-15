"""Оркестратор пайплайна: PERCEPTION -> UNDERSTANDING -> DECISION -> ACTION (+ логирование для LEARNING).

process_message() — единственная точка входа для каналов (web, Telegram).

Порядок и гарантии времени (Ст. 9):
  1. normalize + pre_classify (без LLM, миллисекунды).
  2. F (инъекция): мгновенный шаблон, инцидент, флаг suspicious; легитимная часть идёт дальше.
  3. I по лексикону: мгновенный шаблон ACK (заглушка Smooth & Hold до Шага 4), без LLM.
  4. Остальное: LLM-часть (Understanding -> Decision -> Action) под таймером 3.5 с.
     Не уложились -> клиенту уходит INTERIM («принял, уточняю»), пайплайн догоняет в фоне
     и кладёт FINAL в messages/outbox.

Все ответы ИИ сохраняются в messages и outbox; каналы забирают их оттуда. Логи/events
проходят через mask_pii (Ст. 4).
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, col, select

from app import models as m
from app.db import session_scope
from core import action, handoff, smooth_hold, templates, tools
from core.decision import decide
from core.learning import log_event as _log_event
from core.llm import LLMProvider, get_llm_provider
from core.perception import INTERIM_TIMEOUT_S, Perceived, normalize, pre_classify
from core.policy import get_policy
from core.privacy import mask_pii, text_hash
from core.schemas import (
    BotMessage,
    BotResponse,
    ConvState,
    Decision,
    IncomingMessage,
    Policy,
    PreClassification,
    Understanding,
    top_category,
    utcnow,
)
from core.understanding import analyze

logger = logging.getLogger(__name__)  # только типы ошибок и служебные данные, без текста клиента

INTERIM_BUDGET_S = INTERIM_TIMEOUT_S  # модульная переменная: тесты могут её уменьшать
HISTORY_TAIL = 6
# Модель/SKU и количество в тексте: нужны, когда клиент не повторяет позицию в новой реплике
# (граница C/I, roadmap п.0 №16).
_SKU_IN_TEXT = re.compile(r"\bPX-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)
_QTY_IN_TEXT = re.compile(
    r"(\d{1,4})\s*(?:шт|штук\w*|единиц\w*|принтер\w*|мфу|сканер\w*|картридж\w*|тонер\w*)",
    re.IGNORECASE,
)
_STATE_FIELDS = {"status", "suspicious", "discount_offer_made", "discount_push_count", "low_conf_streak", "category"}
_CARD_FIELDS = {"product", "qty", "budget", "discount_pct", "stage", "notes"}
_BACKGROUND: set[asyncio.Task[Any]] = set()


@dataclass
class TurnContext:
    """Контекст обработки одного входящего сообщения."""

    conversation_id: int
    client_id: int
    channel: str
    message_id: int
    policy: Policy  # актуальная версия, прочитана из БД для ЭТОГО сообщения (Ст. 2)
    state: ConvState
    history: list[tuple[str, str]]
    first_reply_logged: bool = field(default=False)


# ---------------------------------------------------------------------------
# Работа с БД
# ---------------------------------------------------------------------------
def _ensure_client(s: Session, client_id: int) -> None:
    if s.get(m.Client, client_id) is None:
        data = tools.get_client(client_id) or {}
        s.add(
            m.Client(
                id=client_id,
                name=data.get("name", f"Клиент {client_id}"),
                phone=data.get("phone"),
                email=data.get("email"),
                company=data.get("company"),
                status=data.get("status", "new"),
                telegram_id=data.get("telegram_id"),
            )
        )
        s.flush()


def _ensure_conversation(s: Session, msg: IncomingMessage) -> m.Conversation:
    if msg.conversation_id is not None:
        conv = s.get(m.Conversation, msg.conversation_id)
        if conv is None or conv.client_id != msg.client_id:
            raise ValueError("Диалог не найден или принадлежит другому клиенту")  # Ст. 4
        return conv
    stmt = (
        select(m.Conversation)
        .where(
            m.Conversation.client_id == msg.client_id,
            m.Conversation.channel == msg.channel,
            m.Conversation.status != "RESOLVED",
        )
        .order_by(col(m.Conversation.id).desc())
    )
    conv = s.exec(stmt).first()
    if conv is None:
        conv = m.Conversation(client_id=msg.client_id, channel=msg.channel)
        s.add(conv)
        s.flush()
    return conv


def _state_of(conv: m.Conversation) -> ConvState:
    return ConvState(
        status=conv.status,
        suspicious=conv.suspicious,
        discount_offer_made=conv.discount_offer_made,
        discount_push_count=conv.discount_push_count,
        low_conf_streak=conv.low_conf_streak,
        category=conv.category,
    )


def _history(s: Session, conversation_id: int, exclude_id: int | None) -> list[tuple[str, str]]:
    stmt = (
        select(m.Message)
        .where(m.Message.conversation_id == conversation_id, m.Message.id != exclude_id)
        .order_by(col(m.Message.id).desc())
        .limit(HISTORY_TAIL)
    )
    return [(row.role, row.text) for row in reversed(list(s.exec(stmt).all()))]


def _notify(s: Session, conversation_id: int, type_: str, text: str) -> None:
    """Уведомление менеджеру. DISPUTE дедуплицируется и дублируется циклом HOLD (smooth_hold)."""
    if type_ == "DISPUTE":
        smooth_hold.notify_dispute(s, conversation_id, text)
        return
    s.add(m.Notification(type=type_, conversation_id=conversation_id, text=text))


def _apply_state(s: Session, ctx: TurnContext, updates: dict[str, Any]) -> None:
    updates = {k: v for k, v in updates.items() if k in _STATE_FIELDS}
    if not updates:
        return
    conv = s.get(m.Conversation, ctx.conversation_id)
    assert conv is not None
    for key, value in updates.items():
        setattr(conv, key, value)
    conv.updated_at = utcnow()
    ctx.state = ctx.state.model_copy(update=updates)


def _apply_card(s: Session, ctx: TurnContext, updates: dict[str, Any]) -> None:
    updates = {k: v for k, v in updates.items() if k in _CARD_FIELDS and v is not None}
    if not updates:
        return
    deal = s.exec(select(m.Deal).where(m.Deal.conversation_id == ctx.conversation_id)).first()
    if deal is None:
        deal = m.Deal(client_id=ctx.client_id, conversation_id=ctx.conversation_id)
        s.add(deal)
    for key, value in updates.items():
        setattr(deal, key, value)
    deal.updated_at = utcnow()


def _record_decision(s: Session, ctx: TurnContext, decision: Decision, confidence: float, summary: str) -> None:
    s.add(
        m.Decision(
            conversation_id=ctx.conversation_id,
            message_id=ctx.message_id,
            category=decision.category,
            route=decision.route,
            confidence=confidence,
            policy_version=decision.policy_version,
            reasons=decision.reasons,
            action_summary=mask_pii(summary)[:500],
        )
    )
    _log_event(
        s, "decision", ctx.conversation_id,
        {"category": decision.category, "route": decision.route, "reasons": decision.reasons,
         "policy_version": decision.policy_version, "clarify": decision.clarify},
    )


def _emit(
    ctx: TurnContext, perceived: Perceived, messages: list[BotMessage], group: str = "other",
    caught_by: str | None = None,
) -> None:
    """Сохраняет ответы ИИ в messages и outbox; фиксирует Time-to-first-response.

    group: "IF" (ACK на спор/инъекцию), "hold" (ответ клиенту в HOLD) или "other" — для метрик TTFR;
    caught_by: "pre_classifier" | "llm" — кто распознал спор/инъекцию.
    """
    with session_scope() as s:
        for bm in messages:
            latency = perceived.elapsed_ms()
            smooth_hold.store_ai_message(
                s, ctx.conversation_id, ctx.channel, bm.text, bm.phase, bm.deterministic, latency
            )
            if not ctx.first_reply_logged:
                _log_event(
                    s, "first_response", ctx.conversation_id,
                    {"phase": bm.phase, "deterministic": bm.deterministic, "group": group,
                     "caught_by": caught_by, "message_id": ctx.message_id},
                    latency,
                )
                ctx.first_reply_logged = True


def _open_turn(perceived: Perceived) -> TurnContext:
    msg = perceived.message
    with session_scope() as s:
        _ensure_client(s, msg.client_id)
        conv = _ensure_conversation(s, msg)
        row = m.Message(conversation_id=conv.id, role="client", text=msg.text)  # type: ignore[arg-type]
        s.add(row)
        s.flush()
        policy = get_policy(s)  # актуальная версия на КАЖДОЕ сообщение
        _log_event(s, "message_received", conv.id, {"text": msg.text, "channel": msg.channel, "message_id": row.id})
        return TurnContext(
            conversation_id=conv.id,  # type: ignore[arg-type]
            client_id=msg.client_id,
            channel=msg.channel,
            message_id=row.id,  # type: ignore[arg-type]
            policy=policy,
            state=_state_of(conv),
            history=_history(s, conv.id, row.id),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Ветки обработки
# ---------------------------------------------------------------------------
def _handle_injection(
    ctx: TurnContext, perceived: Perceived, out: BotResponse, patterns: list[str], legit_text: str | None,
    caught_by: str = "pre_classifier",
) -> None:
    """Категория F (Ст. 3): детерминированный шаблон, инцидент, suspicious, уведомление безопасности."""
    raw = perceived.message.text
    reply = BotMessage(text=templates.f_script(has_legit_part=bool(legit_text)), phase="ACK", deterministic=True)
    decision = Decision(
        category="F", route="INJECTION", reasons=patterns[:5], policy_version=ctx.policy.version,
        state_updates={"suspicious": True, "category": top_category(ctx.state.category, "F")},
    )
    with session_scope() as s:
        s.add(
            m.SecurityIncident(
                conversation_id=ctx.conversation_id,
                raw_text_masked=mask_pii(raw)[:1000],
                text_hash=text_hash(raw),
                pattern=",".join(patterns)[:200],
            )
        )
        _notify(s, ctx.conversation_id, "SECURITY", "Попытка prompt injection: " + ", ".join(patterns[:3]))
        _apply_state(s, ctx, decision.state_updates)
        _record_decision(s, ctx, decision, 1.0, reply.text)
        _log_event(s, "injection_detected", ctx.conversation_id, {"patterns": patterns, "caught_by": caught_by})
    _emit(ctx, perceived, [reply], "IF", caught_by)
    out.messages.append(reply)


def _handle_dispute(
    ctx: TurnContext, perceived: Perceived, out: BotResponse, decision: Decision, confidence: float,
    caught_by: str = "pre_classifier",
) -> None:
    """Категория I (Ст. 6): мгновенный ACK-шаблон, статус HOLD, уведомление менеджеру, саммари."""
    reply = BotMessage(text=smooth_hold.ack(ctx.policy), phase="ACK", deterministic=True)
    with session_scope() as s:
        _notify(s, ctx.conversation_id, "DISPUTE", "Сложный диалог: клиент ждёт менеджера")
        _apply_state(s, ctx, decision.state_updates)
        conv = s.get(m.Conversation, ctx.conversation_id)
        assert conv is not None
        conv.last_ai_ping_ts = utcnow()  # отсчёт для hold-пингов клиенту
        _record_decision(s, ctx, decision, confidence, reply.text)
        _log_event(s, "dispute_hold", ctx.conversation_id, {"reasons": decision.reasons, "caught_by": caught_by})
    _emit(ctx, perceived, [reply], "IF", caught_by)
    with session_scope() as s:  # саммари готовится сразу, не дожидаясь менеджера (Ст. 8)
        handoff.refresh_handoff(s, ctx.conversation_id, ctx.policy)
    out.messages.append(reply)


def _remembered_order(ctx: TurnContext, und: Understanding) -> tuple[str | None, int | None, float | None]:
    """Позиция и количество из контекста диалога: карточка сделки, затем прошлые сообщения.

    Нужно для границы C/I (roadmap п.0 №16): клиент не повторяет модель в каждой реплике
    («всё равно нужна скидка 20%»), а расчёт допустимой скидки обязан остаться тем же.
    """
    query = und.entities.sku or und.entities.product
    qty, budget = und.entities.qty, und.entities.budget
    with session_scope() as s:
        deal = s.exec(select(m.Deal).where(m.Deal.conversation_id == ctx.conversation_id)).first()
    if deal is not None:
        query = query or deal.product
        qty = qty if qty is not None else deal.qty
        budget = budget if budget is not None else deal.budget
    if not query or qty is None:  # ищем в прошлых репликах: «…на 10 штук PX-M4200»
        for role, text in reversed(ctx.history):
            if role != "client":
                continue
            if query is None and (match := _SKU_IN_TEXT.search(text)):
                query = match.group(0).upper()
            if qty is None and (qty_match := _QTY_IN_TEXT.search(text)):
                qty = int(qty_match.group(1))
            if query is not None and qty is not None:
                break
    return query, qty, budget


def _decision_inputs(
    ctx: TurnContext, und: Understanding, pre: PreClassification, text: str
) -> tuple[float | None, bool | None, dict[str, Any] | None]:
    """Факты для DECISION: допустимая скидка (по Policy), наличие шаблона КП и расчёт по заказу."""
    allowed: float | None = None
    order: dict[str, Any] | None = None
    if "C" in und.category_candidates or pre.discount_request_pct is not None:
        inputs = action.discount_inputs(und, text)
        if not (und.entities.sku or und.entities.product):
            # Позиция в текущем сообщении не названа — берём её из диалога.
            query, qty, budget = _remembered_order(ctx, und)
            inputs["query"] = query or inputs["query"]
            inputs["qty"] = inputs["qty"] if inputs.get("qty") is not None else qty
            inputs["budget"] = inputs["budget"] if inputs.get("budget") is not None else budget
        order = tools.calc_discount(ctx.client_id, ctx.policy, **inputs)
        allowed = order["allowed_pct"]
    has_template: bool | None = None
    people = und.entities.people_count
    if "D" in und.category_candidates and people is not None:
        has_template = tools.find_quote_template(ctx.client_id, people) is not None
    return allowed, has_template, order


async def _run_pipeline(
    ctx: TurnContext, perceived: Perceived, text: str, pre: PreClassification, llm: LLMProvider
) -> BotResponse:
    """LLM-часть: Understanding -> Decision -> Action. Все результаты сохраняет сама."""
    out = BotResponse(conversation_id=ctx.conversation_id)
    try:
        und = await asyncio.to_thread(analyze, text, ctx.history, llm)
        allowed, has_template, order = _decision_inputs(ctx, und, pre, text)
        decision = decide(pre, und, ctx.state, ctx.policy, allowed_pct=allowed, has_template=has_template)

        if decision.route == "INJECTION":  # LLM заметила манипуляцию, которую не поймал regex
            _handle_injection(ctx, perceived, out, ["llm_flag"], None, "llm")
            return out
        if decision.route == "SMOOTH_HOLD":
            _handle_dispute(ctx, perceived, out, decision, und.confidence, "llm")
            return out

        with session_scope() as s:  # состояние (например, discount_offer_made) фиксируем до ответа
            _apply_state(s, ctx, decision.state_updates)
        result = await asyncio.to_thread(
            action.execute, decision, und, text, ctx.client_id, ctx.state, ctx.policy, llm, ctx.history
        )
        if order is not None:  # позиция, по которой считали скидку, попадает в карточку сделки
            for key in ("product", "qty"):
                if order.get(key) is not None:
                    result.card_updates.setdefault(key, order[key])
        with session_scope() as s:
            _record_decision(s, ctx, decision, und.confidence, result.messages[0].text if result.messages else "")
            _apply_card(s, ctx, result.card_updates)
            for note in result.notifications:
                _notify(s, ctx.conversation_id, note["type"], note.get("text", ""))
            for event in result.events:
                _log_event(s, event["type"], ctx.conversation_id, event.get("payload", {}))
        _emit(ctx, perceived, result.messages)
        out.messages.extend(result.messages)
        out.card_updates.update(result.card_updates)
    except Exception as exc:  # клиент не должен остаться без ответа (Ст. 9)
        logger.error("pipeline error: %s", type(exc).__name__)
        reply = BotMessage(text=templates.generic_fallback(), phase="FINAL", deterministic=True)
        with session_scope() as s:
            _log_event(s, "pipeline_error", ctx.conversation_id, {"error": type(exc).__name__})
        _emit(ctx, perceived, [reply])
        out.messages.append(reply)
    return out


async def process_message(msg: IncomingMessage, llm: LLMProvider | None = None) -> BotResponse:
    """Обрабатывает сообщение клиента. Возвращает то, что готово к моменту таймера 3.5 с;
    поздние ответы (после INTERIM) сохраняются в messages/outbox."""
    provider = llm or get_llm_provider()
    perceived = normalize(msg)
    text = perceived.message.text
    ctx = _open_turn(perceived)
    out = BotResponse(conversation_id=ctx.conversation_id)

    if not text:
        reply = BotMessage(text=templates.generic_fallback(), phase="FINAL", deterministic=True)
        _emit(ctx, perceived, [reply])
        out.messages.append(reply)
        return out

    pre = pre_classify(text)

    # F: инъекция — шаблон мгновенно; легитимная часть (если есть) обрабатывается дальше
    if pre.risk == "F":
        _handle_injection(ctx, perceived, out, [x.removeprefix("injection:") for x in pre.markers], pre.legit_text)
        if not pre.legit_text:
            return out
        text = pre.legit_text
        pre = pre_classify(text)

    # Менеджер ведёт диалог — ИИ молчит (кроме обработки инъекций выше)
    if ctx.state.status == "HANDOFF":
        with session_scope() as s:
            _log_event(s, "handoff_silent", ctx.conversation_id, {"message_id": ctx.message_id})
        return out

    # Диалог ждёт менеджера — SMOOTH: удерживаем клиента, не оставляем без ответа (Ст. 6)
    if ctx.state.status == "HOLD":
        reply_text, deterministic, events = await asyncio.to_thread(
            smooth_hold.smooth_reply, text, ctx.client_id, ctx.policy, provider, ctx.history
        )
        reply = BotMessage(text=reply_text, phase="HOLD", deterministic=deterministic)
        with session_scope() as s:
            conv = s.get(m.Conversation, ctx.conversation_id)
            assert conv is not None
            conv.last_ai_ping_ts = utcnow()  # следующий hold-пинг отсчитывается от этого ответа
            for event in events:
                _log_event(s, event["type"], ctx.conversation_id, event.get("payload", {}))
            _log_event(s, "hold_reply", ctx.conversation_id, {"deterministic": deterministic})
        _emit(ctx, perceived, [reply], "hold")
        with session_scope() as s:  # новые детали клиента попадают в саммари
            handoff.refresh_handoff(s, ctx.conversation_id, ctx.policy)
        out.messages.append(reply)
        return out

    # I по лексикону: мгновенный ACK без LLM
    if pre.risk == "I":
        decision = decide(pre, Understanding(), ctx.state, ctx.policy)
        _handle_dispute(ctx, perceived, out, decision, 1.0)
        return out

    # Остальное: LLM-часть под таймером
    task = asyncio.create_task(_run_pipeline(ctx, perceived, text, pre, provider))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    try:
        result = await asyncio.wait_for(asyncio.shield(task), timeout=perceived.remaining_s(INTERIM_BUDGET_S))
        out.messages.extend(result.messages)
        out.card_updates.update(result.card_updates)
    except asyncio.TimeoutError:
        interim = BotMessage(text=templates.interim_ack(), phase="INTERIM", deterministic=True)
        with session_scope() as s:
            _log_event(s, "interim_timeout", ctx.conversation_id, {})
        _emit(ctx, perceived, [interim])
        out.messages.append(interim)
    return out


async def drain_background() -> None:
    """Ждёт завершения фоновых пайплайнов (после INTERIM). Нужен тестам и CLI."""
    while _BACKGROUND:
        await asyncio.gather(*list(_BACKGROUND), return_exceptions=True)
