"""Ручки заявок из внешних каналов (Telegram-бот-анкета).

Путь под /api/leads, защита — общий сервисный токен `X-Bot-Token` (BOT_API_TOKEN из .env).
Если токен не задан, ручка отвечает 503: заявки с ПДн наружу по умолчанию не принимаются (Ст. 4).

    POST /api/leads   — принять анкету и завести/обновить карточку клиента
    GET  /api/leads   — последние заявки (то, что уже превратилось в клиентов) для диагностики

Бот шлёт сюда те же данные, что видит в переписке: имя, компания, телефон, e-mail, запрос,
telegram_id. Карточка клиента сразу становится доступной сайту (`/api/site/clients`), а в
ленте уведомлений менеджера появляется запись — так заявка попадает в работу менеджера.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import select

from app import models as m
from app.db import session_scope
from core.config import get_settings
from core.leads import LeadError, apply_lead, build_lead

router = APIRouter(prefix="/api/leads", tags=["leads"])


def require_bot_token(x_bot_token: Annotated[str | None, Header()] = None) -> None:
    """Защита ручек бота: сервисный токен из .env (у бота он же в BOT_API_TOKEN)."""
    token = get_settings().bot_api_token
    if not token:
        raise HTTPException(status_code=503, detail="приём заявок выключен: не задан BOT_API_TOKEN")
    if x_bot_token != token:
        raise HTTPException(status_code=401, detail="неверный X-Bot-Token")


BotOnly = Depends(require_bot_token)


class LeadBody(BaseModel):
    """Анкета из бота. Обязательно только имя; контакты проверяет core/leads."""

    name: str = Field(min_length=1)
    company: str | None = None
    phone: str | None = None
    email: str | None = None
    request: str | None = None
    telegram_id: str | int | None = None
    telegram_username: str | None = None
    source: str = "telegram"


@router.post("", dependencies=[BotOnly])
def create_lead(body: LeadBody) -> dict[str, Any]:
    """Принимает анкету, заводит карточку клиента и уведомляет менеджера."""
    try:
        lead = build_lead(
            name=body.name,
            company=body.company,
            phone=body.phone,
            email=body.email,
            request=body.request,
            telegram_id=body.telegram_id,
            telegram_username=body.telegram_username,
            source=body.source,
        )
    except LeadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **apply_lead(lead)}


@router.get("", dependencies=[BotOnly])
def list_leads(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """Последние заявки: по событию `lead_received` видно, кого и когда завели."""
    with session_scope() as session:
        rows = session.exec(
            select(m.Event).where(m.Event.type == "lead_received").order_by(m.Event.id.desc()).limit(limit)
        ).all()
        items = [
            {
                "client_id": (row.payload or {}).get("client_id"),
                "created": (row.payload or {}).get("created"),
                "company": (row.payload or {}).get("company"),
                "at": row.ts.isoformat(timespec="seconds"),
            }
            for row in rows
        ]
    return {"leads": items, "total": len(items)}
