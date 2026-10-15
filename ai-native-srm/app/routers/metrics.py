"""Метрики, инциденты безопасности и журнал решений (Шаг 5, страница 4_Security_Metrics).

Метрики считает core/learning.py; роутер только отдаёт их наружу. Инциденты безопасности
отдаются в МАСКИРОВАННОМ виде — сырой текст остаётся в закрытой таблице messages (Ст. 4).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlmodel import col, select

from app import models as m
from app.db import session_scope
from core.learning import compute_metrics

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/metrics")
def metrics(since: datetime | None = None) -> dict[str, Any]:
    """7 метрик ТЗ + TTFR раздельно + auto-decision rate + override_rate + рекомендации."""
    return compute_metrics(since=since)


@router.get("/security/incidents")
def incidents(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    """Инциденты prompt injection: только маскированный текст и хэш (Ст. 3, 4)."""
    with session_scope() as s:
        rows = list(s.exec(select(m.SecurityIncident).order_by(col(m.SecurityIncident.id).desc()).limit(limit)).all())
        return [
            {
                "id": row.id,
                "conversation_id": row.conversation_id,
                "raw_text_masked": row.raw_text_masked,
                "text_hash": row.text_hash[:12],
                "pattern": row.pattern,
                "ts": row.ts.isoformat(),
            }
            for row in rows
        ]


@router.get("/decisions")
def decisions(
    conversation_id: int | None = None,
    category: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Журнал автономных решений: категория, маршрут, версия политики, override (Ст. 5)."""
    with session_scope() as s:
        stmt = select(m.Decision)
        if conversation_id is not None:
            stmt = stmt.where(m.Decision.conversation_id == conversation_id)
        if category is not None:
            stmt = stmt.where(m.Decision.category == category)
        rows = list(s.exec(stmt.order_by(col(m.Decision.id).desc()).limit(limit)).all())
        return [
            {
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
                "overridden_at": row.overridden_at.isoformat() if row.overridden_at else None,
                "ts": row.ts.isoformat(),
            }
            for row in rows
        ]
