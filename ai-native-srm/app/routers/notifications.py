"""Уведомления менеджеру (Шаг 5): список, отметка «прочитано».

Повторы до takeover — работа hold_loop: одна активная строка на диалог, растёт
reminder_level, а не количество записей (п. 0 №17 роадмапа).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlmodel import col, select

from app import models as m
from app.db import session_scope
from core.schemas import utcnow

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _dict(row: m.Notification, client_name: str | None = None) -> dict[str, Any]:
    waited_min = int((utcnow() - row.created_at).total_seconds() // 60)
    return {
        "id": row.id,
        "type": row.type,  # DISPUTE | SECURITY | OVERRIDE_DUE | REMINDER
        "conversation_id": row.conversation_id,
        "client_name": client_name,
        "text": row.text,
        "read": row.read,
        "active": row.active,
        "reminder_level": row.reminder_level,
        "waited_min": waited_min,
        "created_at": row.created_at.isoformat(),
        "last_notified_at": row.last_notified_at.isoformat(),
    }


@router.get("")
def list_notifications(
    unread_only: bool = Query(default=False),
    active_only: bool = Query(default=True),
    type: str | None = Query(default=None, description="DISPUTE | SECURITY | OVERRIDE_DUE | REMINDER"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Уведомления для панели менеджера: по умолчанию — активные."""
    with session_scope() as s:
        stmt = select(m.Notification)
        if active_only:
            stmt = stmt.where(m.Notification.active == True)  # noqa: E712
        if unread_only:
            stmt = stmt.where(m.Notification.read == False)  # noqa: E712
        if type is not None:
            stmt = stmt.where(m.Notification.type == type)
        rows = list(s.exec(stmt.order_by(col(m.Notification.last_notified_at).desc()).limit(limit)).all())
        names = {c.id: c.name for c in s.exec(select(m.Client)).all()}
        convs = {c.id: c.client_id for c in s.exec(select(m.Conversation)).all()}
        return [
            _dict(row, names.get(convs.get(row.conversation_id or -1, -1))) for row in rows
        ]


@router.post("/{notification_id}/read")
def mark_read(notification_id: int) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(m.Notification, notification_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Уведомление не найдено")
        row.read = True
        return {"id": row.id, "read": True}


@router.post("/read-all")
def mark_all_read() -> dict[str, int]:
    """Массовая отметка «прочитано» — кнопка «разобрать всё» на дашборде."""
    with session_scope() as s:
        rows = list(s.exec(select(m.Notification).where(m.Notification.read == False)).all())  # noqa: E712
        for row in rows:
            row.read = True
        return {"marked": len(rows)}
