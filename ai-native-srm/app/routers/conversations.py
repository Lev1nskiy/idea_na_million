"""Дашборд менеджера: диалоги, takeover, ответ клиенту, override, resolve (Шаг 5).

Все действия менеджера собраны здесь же (handoff.takeover / manager_reply / resolve),
поэтому UI не содержит бизнес-логики — только вызовы этих эндпоинтов.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import col, select

from app import models as m
from app.db import session_scope
from core import handoff
from core.learning import log_event
from core.policy import get_policy
from core.schemas import HandoffSummary, utcnow

router = APIRouter(prefix="/api", tags=["conversations"])

ACTIVE_STATUSES = ("ACTIVE", "HOLD", "HANDOFF")
WAITING_STATUSES = ("ACTIVE", "HOLD", "HANDOFF", "RESOLVED", "LOST")


class TakeoverRequest(BaseModel):
    manager: str = Field(default="Менеджер", max_length=100)


class ManagerReplyRequest(BaseModel):
    manager: str = Field(default="Менеджер", max_length=100)
    text: str = Field(min_length=1, max_length=4000)


class OverrideRequest(BaseModel):
    manager: str = Field(default="Менеджер", max_length=100)
    reason: str = Field(min_length=3, max_length=500)


def _client_names(session) -> dict[int, str]:  # type: ignore[no-untyped-def]
    return {row.id: row.name for row in session.exec(select(m.Client)).all()}


def _last_message(session, conversation_id: int) -> m.Message | None:  # type: ignore[no-untyped-def]
    return session.exec(
        select(m.Message).where(m.Message.conversation_id == conversation_id).order_by(col(m.Message.id).desc())
    ).first()


@router.get("/conversations")
def list_conversations(
    status: str | None = Query(default=None, description="ACTIVE | HOLD | HANDOFF | RESOLVED | LOST"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Список диалогов для дашборда: карточка-строка с последним сообщением и счётчиком уведомлений."""
    if status is not None and status not in WAITING_STATUSES:
        raise HTTPException(status_code=422, detail=f"неизвестный статус: {status}")
    with session_scope() as s:
        stmt = select(m.Conversation)
        if status is not None:
            stmt = stmt.where(m.Conversation.status == status)
        conversations = list(s.exec(stmt.order_by(col(m.Conversation.updated_at).desc()).limit(limit)).all())
        names = _client_names(s)
        out: list[dict[str, Any]] = []
        for conv in conversations:
            last = _last_message(s, conv.id or 0)
            unread = s.exec(
                select(m.Notification).where(
                    m.Notification.conversation_id == conv.id,
                    m.Notification.active == True,  # noqa: E712
                    m.Notification.read == False,  # noqa: E712
                )
            ).all()
            out.append(
                {
                    "id": conv.id,
                    "client_id": conv.client_id,
                    "client_name": names.get(conv.client_id, f"Клиент {conv.client_id}"),
                    "channel": conv.channel,
                    "status": conv.status,
                    "category": conv.category,
                    "suspicious": conv.suspicious,
                    "assigned_manager": conv.assigned_manager,
                    "unread_notifications": len(unread),
                    "last_text": (last.text[:160] if last else None),
                    "last_role": last.role if last else None,
                    "last_ts": last.ts.isoformat() if last else None,
                    "updated_at": conv.updated_at.isoformat(),
                }
            )
        return out


@router.get("/conversations/{conversation_id}")
def conversation_detail(conversation_id: int) -> dict[str, Any]:
    """Карточка диалога: клиент, сделка, саммари Handoff, решения, уведомления."""
    with session_scope() as s:
        conv = s.get(m.Conversation, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        client = s.get(m.Client, conv.client_id)
        deal = s.exec(select(m.Deal).where(m.Deal.conversation_id == conversation_id)).first()
        row = s.exec(
            select(m.Handoff)
            .where(m.Handoff.conversation_id == conversation_id, m.Handoff.status != "CLOSED")
            .order_by(col(m.Handoff.id).desc())
        ).first()
        if row is not None:
            summary: HandoffSummary | None = HandoffSummary.model_validate(row.summary_json)
            handoff_status, taken_by = row.status, row.taken_by
        else:
            # Саммари ещё не собиралось: строим без LLM (дешёвая структурная версия)
            summary = handoff.build_summary(s, conversation_id, get_policy(s)) if conv.status != "ACTIVE" else None
            handoff_status, taken_by = None, None
        decisions = list(
            s.exec(
                select(m.Decision)
                .where(m.Decision.conversation_id == conversation_id)
                .order_by(col(m.Decision.id).desc())
                .limit(20)
            ).all()
        )
        notes = list(
            s.exec(
                select(m.Notification)
                .where(m.Notification.conversation_id == conversation_id)
                .order_by(col(m.Notification.id).desc())
                .limit(20)
            ).all()
        )
        return {
            "conversation": {
                "id": conv.id,
                "client_id": conv.client_id,
                "channel": conv.channel,
                "status": conv.status,
                "category": conv.category,
                "suspicious": conv.suspicious,
                "discount_push_count": conv.discount_push_count,
                "assigned_manager": conv.assigned_manager,
                "created_at": conv.created_at.isoformat(),
                "updated_at": conv.updated_at.isoformat(),
            },
            "client": {"id": client.id, "name": client.name, "status": client.status, "company": client.company}
            if client
            else None,
            "deal": {
                "id": deal.id,
                "product": deal.product,
                "qty": deal.qty,
                "budget": deal.budget,
                "discount_pct": deal.discount_pct,
                "stage": deal.stage,
                "notes": deal.notes,
            }
            if deal
            else None,
            "handoff": {
                "status": handoff_status,
                "taken_by": taken_by,
                "summary": summary.model_dump() if summary else None,
            },
            "decisions": [_decision_dict(d) for d in decisions],
            "notifications": [_notification_dict(n) for n in notes],
        }


@router.post("/conversations/{conversation_id}/takeover")
def takeover(conversation_id: int, body: TakeoverRequest) -> dict[str, Any]:
    """Менеджер берёт диалог: HANDOFF, напоминания прекращаются, клиенту уходит сигнал."""
    try:
        summary = handoff.takeover(conversation_id, body.manager)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    with session_scope() as s:
        conv = s.get(m.Conversation, conversation_id)
        assert conv is not None
        s.add(
            m.Outbox(
                channel=conv.channel,
                conversation_id=conversation_id,
                text="К диалогу подключился менеджер.",
                phase="FINAL",
            )
        )
    return {"conversation_id": conversation_id, "status": "HANDOFF", "manager": body.manager, "summary": summary.model_dump()}


@router.post("/conversations/{conversation_id}/manager-reply")
def reply(conversation_id: int, body: ManagerReplyRequest) -> dict[str, Any]:
    """Ответ менеджера: попадает в messages и outbox, ИИ в этом диалоге замолкает."""
    try:
        handoff.manager_reply(conversation_id, body.manager, body.text)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"conversation_id": conversation_id, "delivered": True}


@router.post("/conversations/{conversation_id}/resolve")
def close_conversation(conversation_id: int) -> dict[str, Any]:
    """Закрывает диалог: уведомления гасятся, Handoff закрывается."""
    try:
        handoff.resolve(conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"conversation_id": conversation_id, "status": "RESOLVED"}


@router.post("/decisions/{decision_id}/override")
def override_decision(decision_id: int, body: OverrideRequest) -> dict[str, Any]:
    """Фиксирует override автономного решения (Ст. 5): кто, почему, когда."""
    with session_scope() as s:
        row = s.get(m.Decision, decision_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Решение не найдено")
        row.overridden_by, row.override_reason, row.overridden_at = body.manager, body.reason, utcnow()
        log_event(s, "override", row.conversation_id, {"decision_id": decision_id, "manager": body.manager})
        return _decision_dict(row)


def _decision_dict(row: m.Decision) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "category": row.category,
        "route": row.route,
        "confidence": row.confidence,
        "policy_version": row.policy_version,
        "reasons": list(row.reasons or []),
        "action_summary": row.action_summary,
        "overridden_by": row.overridden_by,
        "override_reason": row.override_reason,
        "ts": row.ts.isoformat(),
    }


def _notification_dict(row: m.Notification) -> dict[str, Any]:
    waited_min = int((utcnow() - row.created_at).total_seconds() // 60)
    return {
        "id": row.id,
        "type": row.type,
        "conversation_id": row.conversation_id,
        "text": row.text,
        "read": row.read,
        "active": row.active,
        "reminder_level": row.reminder_level,
        "waited_min": waited_min,
        "created_at": row.created_at.isoformat(),
        "last_notified_at": row.last_notified_at.isoformat(),
    }
