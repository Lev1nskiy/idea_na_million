"""SMOOTH & HOLD (Ст. 6): удержание клиента в споре до подключения менеджера.

- ack()          — мгновенный шаблон без LLM (ACKNOWLEDGE).
- smooth_reply() — LLM-ответ на сообщения клиента в HOLD, проверяется V1/V3/V4/V5/V6/V7.
- run_hold_cycle()/hold_loop() — периодические пинги клиенту и дублирование уведомления
  менеджеру до takeover/resolve.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app import models as m
from app.db import session_scope
from core import templates, tools
from core.guardrails import numbers_in, validate_output
from core.learning import log_event
from core.llm import LLMError, LLMProvider
from core.llm.base import default_timeout
from core.policy import get_manager_sla, get_policy
from core.prompts.generation import build_generation_prompt, render_template
from core.prompts.system import build_system_prompt
from core.schemas import Policy, utcnow

logger = logging.getLogger(__name__)

SMOOTH_BUDGET_S = 3.0  # общий бюджет на LLM-ответ в HOLD; затем шаблон (Ст. 9: < 5 с)
GEN_TIMEOUT_S = 2.0  # запасное значение: рабочий бюджет берётся из LLM_TIMEOUT_S


# ---------------------------------------------------------------------------
# ACKNOWLEDGE
# ---------------------------------------------------------------------------
def ack(policy: Policy, now: datetime | None = None) -> str:
    """Шаблон ACK без LLM: признание проблемы, эмпатия, честный SLA из Policy, remedy как ориентир."""
    return templates.ack_dispute(get_manager_sla(policy, now), templates.remedy_labels(policy.remedy_options))


# ---------------------------------------------------------------------------
# SMOOTH
# ---------------------------------------------------------------------------
def smooth_reply(
    text: str,
    client_id: int,
    policy: Policy,
    llm: LLMProvider,
    history: Sequence[tuple[str, str]] = (),
    now: datetime | None = None,
) -> tuple[str, bool, list[dict[str, Any]]]:
    """Ответ клиенту, который ждёт менеджера. Возвращает (текст, это_шаблон, события).

    Запрещено: признание вины, обязательства по remedy, спор по существу, придуманные сроки.
    Обязательно: эмпатия, честный срок из Policy, remedy как ориентир (проверка V6).
    """
    current_sla = templates.sla_phrase(get_manager_sla(policy, now))
    facts = {"sla_phrase": current_sla, "remedy_options": templates.remedy_labels(policy.remedy_options)}
    numbers = numbers_in(facts)
    foreign = tools.foreign_client_markers(client_id)
    system = build_system_prompt(policy)
    events: list[dict[str, Any]] = []
    deadline = time.monotonic() + SMOOTH_BUDGET_S
    hint = ""

    for attempt in (1, 2):
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            break
        user = build_generation_prompt("smooth_hold", facts, text, history, (), hint)
        try:
            candidate = llm.complete_text(
                # Ограничение сверху — остаток «окна», снизу — общий бюджет модели
                system, user, timeout=min(default_timeout(), remaining), temperature=0.3
            ).strip()
        except LLMError as exc:
            events.append({"type": "llm_error", "payload": {"stage": "smooth", "error": type(exc).__name__}})
            break
        found = (
            validate_output(
                candidate, whitelist=numbers, client_text=text, policy=policy,
                foreign_markers=foreign, sla_phrases=[current_sla], smooth=True,
            )
            if candidate
            else []
        )
        if candidate and not found:
            return candidate, False, events
        events.append(
            {"type": "validator_block",
             "payload": {"scenario": "smooth_hold", "attempt": attempt, "codes": sorted({v.code for v in found}) or ["EMPTY"]}}
        )
        hint = "; ".join(v.hint for v in found) or "ответ пустой"
    return render_template("smooth_hold", facts), True, events


# ---------------------------------------------------------------------------
# Хранение ответов и уведомлений
# ---------------------------------------------------------------------------
def store_ai_message(
    s: Session, conversation_id: int, channel: str, text: str, phase: str, deterministic: bool,
    latency_ms: int | None = None,
) -> None:
    """Сохраняет ответ ИИ в messages и outbox (каналы забирают оттуда)."""
    s.add(m.Message(conversation_id=conversation_id, role="ai", text=text, phase=phase,
                    deterministic=deterministic, latency_ms=latency_ms))
    s.add(m.Outbox(channel=channel, conversation_id=conversation_id, text=text, phase=phase))


def notify_dispute(s: Session, conversation_id: int, text: str) -> m.Notification:
    """Одно активное уведомление DISPUTE на диалог: повтор обновляет last_notified_at."""
    stmt = select(m.Notification).where(
        m.Notification.conversation_id == conversation_id,
        m.Notification.type == "DISPUTE",
        m.Notification.active == True,  # noqa: E712
    )
    existing = s.exec(stmt).first()
    if existing is not None:
        existing.last_notified_at = utcnow()
        return existing
    note = m.Notification(type="DISPUTE", conversation_id=conversation_id, text=text)
    s.add(note)
    return note


# ---------------------------------------------------------------------------
# HOLD: напоминания менеджеру и пинги клиенту
# ---------------------------------------------------------------------------
def run_hold_cycle(now: datetime | None = None) -> dict[str, int]:
    """Один проход цикла HOLD (детерминирован: now можно подменить в тестах).

    1) каждые reminder_minutes повторяет уведомление менеджеру: reminder_level растёт,
       last_notified_at обновляется, строка остаётся одна;
    2) каждые hold_ping_minutes отправляет клиенту пинг (last_ai_ping_ts защищает от спама).
    Диалоги не в статусе HOLD (takeover/resolve) не затрагиваются.
    """
    moment = now or utcnow()
    reminders = pings = 0
    with session_scope() as s:
        policy = get_policy(s)
        th = policy.thresholds
        holds = s.exec(select(m.Conversation).where(m.Conversation.status == "HOLD")).all()
        for conv in holds:
            note = s.exec(
                select(m.Notification).where(
                    m.Notification.conversation_id == conv.id,
                    m.Notification.type == "DISPUTE",
                    m.Notification.active == True,  # noqa: E712
                )
            ).first()
            if note is not None and moment - note.last_notified_at >= timedelta(minutes=th.reminder_minutes):
                note.reminder_level += 1
                note.last_notified_at = moment
                note.read = False
                waited = int((moment - note.created_at).total_seconds() // 60)
                overdue = waited > get_manager_sla(policy, moment).response_hours * 60
                note.text = f"Клиент ждёт менеджера {waited} мин (напоминание {note.reminder_level})" + (
                    " — срок SLA просрочен" if overdue else ""
                )
                log_event(
                    s, "manager_reminder", conv.id,
                    {"level": note.reminder_level, "waited_min": waited, "overdue": overdue,
                     "telegram": note.reminder_level >= 2},
                )
                reminders += 1

            reference = conv.last_ai_ping_ts or conv.updated_at
            if moment - reference >= timedelta(minutes=th.hold_ping_minutes):
                text = templates.hold_ping(get_manager_sla(policy, moment), templates.remedy_labels(policy.remedy_options))
                store_ai_message(s, conv.id, conv.channel, text, "HOLD", True)  # type: ignore[arg-type]
                conv.last_ai_ping_ts = moment
                log_event(s, "hold_ping", conv.id, {})
                pings += 1
    return {"reminders": reminders, "pings": pings}


async def hold_loop(stop: asyncio.Event, tick_s: float = 30.0) -> None:
    """Фоновая задача: периодически запускает run_hold_cycle до установки stop."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(run_hold_cycle)
        except Exception as exc:  # цикл не должен падать из-за разовой ошибки
            logger.error("hold_loop: %s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=tick_s)
        except asyncio.TimeoutError:
            pass
