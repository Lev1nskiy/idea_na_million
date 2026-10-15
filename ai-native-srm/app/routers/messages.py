"""Вход сообщений клиента и поллинг ответов (Шаг 5).

POST /api/messages — единственная точка входа для каналов (web, Telegram). Возвращает то,
что готово к моменту ответа: детерминированный ACK/F (мгновенно) или FINAL; если пайплайн
не уложился в 3.5 с — фазу INTERIM, а FINAL доедет до клиента через outbox и попадёт в
GET /api/conversations/{id}/messages.

GET /api/outbox — для каналов-адаптеров (Telegram, Шаг 6): батч ещё не доставленных
сообщений; факт выдачи отмечается флагом delivered (доставка «at-least-once»).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import col, select

from app import models as m
from app.db import session_scope
from core import ai_engine
from core.schemas import BotResponse, IncomingMessage

router = APIRouter(prefix="/api", tags=["messages"])


class MessageOut(BaseModel):
    """Сообщение для поллинга; текст отдаётся только здесь и только по этому диалогу (Ст. 4)."""

    id: int
    role: str  # client | ai | manager
    text: str
    phase: str | None = None
    deterministic: bool = False
    latency_ms: int | None = None
    ts: str

    @classmethod
    def of(cls, row: m.Message) -> "MessageOut":
        return cls(
            id=row.id or 0,
            role=row.role,
            text=row.text,
            phase=row.phase,
            deterministic=row.deterministic,
            latency_ms=row.latency_ms,
            ts=row.ts.isoformat(),
        )


class OutboxItem(BaseModel):
    id: int
    channel: str
    conversation_id: int
    text: str
    phase: str | None = None
    ts: str


@router.post("/messages", response_model=BotResponse)
async def post_message(msg: IncomingMessage) -> BotResponse:
    """Принимает сообщение клиента и возвращает готовую часть ответа (ACK/INTERIM/FINAL)."""
    try:
        return await ai_engine.process_message(msg)
    except ValueError as exc:  # диалог не найден / принадлежит другому клиенту (Ст. 4)
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: int,
    after: int = Query(default=0, ge=0, description="id, после которого вернуть сообщения (поллинг)"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[MessageOut]:
    """Сообщения диалога по возрастанию id — клиент чата и дашборд опрашивают этот эндпоинт."""
    with session_scope() as s:
        if s.get(m.Conversation, conversation_id) is None:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        stmt = (
            select(m.Message)
            .where(m.Message.conversation_id == conversation_id, col(m.Message.id) > after)
            .order_by(col(m.Message.id))
            .limit(limit)
        )
        return [MessageOut.of(row) for row in s.exec(stmt).all()]


@router.get("/outbox", response_model=list[OutboxItem])
def pull_outbox(
    channel: str = Query(default="telegram"),
    conversation_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[OutboxItem]:
    """Отдаёт недоставленные сообщения канала и помечает их delivered (адаптеры Шага 6)."""
    with session_scope() as s:
        stmt = select(m.Outbox).where(m.Outbox.channel == channel, m.Outbox.delivered == False)  # noqa: E712
        if conversation_id is not None:
            stmt = stmt.where(m.Outbox.conversation_id == conversation_id)
        rows = list(s.exec(stmt.order_by(col(m.Outbox.id)).limit(limit)).all())
        for row in rows:
            row.delivered = True
        return [
            OutboxItem(
                id=row.id or 0,
                channel=row.channel,
                conversation_id=row.conversation_id,
                text=row.text,
                phase=row.phase,
                ts=row.ts.isoformat(),
            )
            for row in rows
        ]
