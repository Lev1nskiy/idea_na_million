"""Клиенты: выбор в чате, справочник для дашборда и привязка Telegram (Шаги 5–6).

Правило приватности: открытые ответы содержат только имя, статус и компанию.
Идентификатор Telegram (`telegram_id`) — персональный идентификатор: он отдаётся
только при валидном заголовке `X-Admin-Token` (Ст. 4). Поиск по telegram_id нужен
адаптеру Telegram (channels/telegram_bot.py) и возвращает лишь `client_id`.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from sqlmodel import col, select

from app import models as m
from app.db import session_scope
from core.config import get_settings

router = APIRouter(prefix="/api", tags=["clients"])


def _admin_token_ok(x_admin_token: str | None) -> bool:
    """Валиден ли админ-токен (пустой ADMIN_TOKEN = админ-доступ выключен)."""
    token = get_settings().admin_token
    return bool(token) and x_admin_token == token


@router.get("/clients")
def list_clients() -> list[dict[str, Any]]:
    """Клиенты для селектора в чате (порядок — по id демо-набора)."""
    with session_scope() as s:
        rows = list(s.exec(select(m.Client).order_by(col(m.Client.id))).all())
        return [
            {"id": row.id, "name": row.name, "status": row.status, "company": row.company}
            for row in rows
        ]


@router.get("/clients/by-telegram/{telegram_id}")
def client_by_telegram(telegram_id: str) -> dict[str, Any]:
    """Находит клиента по telegram_id: адаптеру нужен только client_id, ничего больше не отдаём."""
    with session_scope() as s:
        row = s.exec(select(m.Client).where(m.Client.telegram_id == telegram_id.strip())).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Клиент с таким Telegram не привязан")
        return {"client_id": row.id}


@router.get("/clients/{client_id}")
def client_detail(
    client_id: int,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Карточка клиента; `telegram_id` — только админ-вызову (бот с валидным токеном)."""
    with session_scope() as s:
        row = s.get(m.Client, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        conversations = list(
            s.exec(select(m.Conversation).where(m.Conversation.client_id == client_id)).all()
        )
        payload: dict[str, Any] = {
            "id": row.id,
            "name": row.name,
            "status": row.status,
            "company": row.company,
            "created_at": row.created_at.isoformat(),
            "conversations": [
                {"id": c.id, "status": c.status, "channel": c.channel, "category": c.category}
                for c in conversations
            ],
        }
        if _admin_token_ok(x_admin_token):
            payload["telegram_id"] = row.telegram_id
        return payload
