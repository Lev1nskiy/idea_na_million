"""Клиенты: выбор в чате и справочник для дашборда (Шаг 5).

Отдаём только неконфиденциальные поля (имя, статус, компания): телефоны/почта в UI
не нужны, а открытых эндпоинтов с ПДн быть не должно (Ст. 4).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from sqlmodel import col, select

from app import models as m
from app.db import session_scope

router = APIRouter(prefix="/api", tags=["clients"])


@router.get("/clients")
def list_clients() -> list[dict[str, Any]]:
    """Клиенты для селектора в чате (порядок — по id демо-набора)."""
    with session_scope() as s:
        rows = list(s.exec(select(m.Client).order_by(col(m.Client.id))).all())
        return [
            {"id": row.id, "name": row.name, "status": row.status, "company": row.company}
            for row in rows
        ]


@router.get("/clients/{client_id}")
def client_detail(client_id: int) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(m.Client, client_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Клиент не найден")
        conversations = list(
            s.exec(select(m.Conversation).where(m.Conversation.client_id == client_id)).all()
        )
        return {
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
