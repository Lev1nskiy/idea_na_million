"""Карточки сделок: список и детали (Шаг 5).

Карточку заполняет ИИ по ходу диалога (action.py -> card_updates), менеджер видит её
на дашборде. Правка карточки руками — через PUT /api/deals/{id} (кейс override).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import col, select

from app import models as m
from app.db import session_scope
from core.schemas import utcnow

router = APIRouter(prefix="/api", tags=["deals"])


class DealUpdate(BaseModel):
    stage: str | None = None
    notes: str | None = Field(default=None, max_length=1000)
    discount_pct: float | None = Field(default=None, ge=0, le=100)


def _deal_dict(row: m.Deal, client_name: str | None = None, conversation_status: str | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "client_id": row.client_id,
        "client_name": client_name,
        "conversation_id": row.conversation_id,
        "conversation_status": conversation_status,
        "product": row.product,
        "qty": row.qty,
        "budget": row.budget,
        "discount_pct": row.discount_pct,
        "stage": row.stage,
        "notes": row.notes,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.get("/deals")
def list_deals(
    stage: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Все карточки сделок (опционально по стадии) — для дашборда и метрик воронки."""
    with session_scope() as s:
        stmt = select(m.Deal)
        if stage:
            stmt = stmt.where(m.Deal.stage == stage)
        deals = list(s.exec(stmt.order_by(col(m.Deal.updated_at).desc()).limit(limit)).all())
        names = {c.id: c.name for c in s.exec(select(m.Client)).all()}
        statuses = {c.id: c.status for c in s.exec(select(m.Conversation)).all()}
        return [
            _deal_dict(d, names.get(d.client_id), statuses.get(d.conversation_id or -1)) for d in deals
        ]


@router.get("/deals/{deal_id}")
def deal_detail(deal_id: int) -> dict[str, Any]:
    with session_scope() as s:
        deal = s.get(m.Deal, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="Сделка не найдена")
        client = s.get(m.Client, deal.client_id)
        return _deal_dict(deal, client.name if client else None)


@router.put("/deals/{deal_id}")
def update_deal(deal_id: int, body: DealUpdate) -> dict[str, Any]:
    """Правка карточки менеджером (stage/notes/скидка) — фиксируется событием для метрик."""
    from core.learning import log_event

    with session_scope() as s:
        deal = s.get(m.Deal, deal_id)
        if deal is None:
            raise HTTPException(status_code=404, detail="Сделка не найдена")
        changes = body.model_dump(exclude_none=True)
        for key, value in changes.items():
            setattr(deal, key, value)
        deal.updated_at = utcnow()
        if changes:
            log_event(s, "deal_updated", deal.conversation_id, {"deal_id": deal_id, "fields": sorted(changes)})
        client = s.get(m.Client, deal.client_id)
        return _deal_dict(deal, client.name if client else None)
