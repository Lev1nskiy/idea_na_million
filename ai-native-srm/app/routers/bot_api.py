"""Фасад для Telegram-бота: всё, что боту нужно от CRM, одним токеном.

Зачем отдельный роутер: бот — такая же внешняя поверхность, как сайт. Сайт ходит через
`ui_bridge` под сессией сотрудника, бот — через `/api/bot/*` под сервисным токеном
`X-Bot-Token` (тот же `BOT_API_TOKEN`, что и у `/api/leads`). Бот не знает ни про сессии,
ни про админ-токен, ни про внутренние ручки дашборда.

Ручки (все — только с валидным `X-Bot-Token`):

    GET  /api/bot/me                      — карточка клиента по telegram_id (или found=false)
    POST /api/bot/me                      — сохранить профиль (заводит карточку, если её не было)
    GET  /api/bot/me/appeals              — мои обращения: статус, тема, последнее сообщение
    GET  /api/bot/me/appeals/{id}         — переписка по обращению (клиент ↔ ИИ ↔ менеджер)
    POST /api/bot/me/appeals/{id}/manager — позвать менеджера: HOLD, саммари, уведомление
    POST /api/bot/me/appeals/{id}/close   — клиент закрыл обращение (resolve)
    POST /api/bot/messages                — сообщение клиента в пайплайн (тот же, что у web)
    POST /api/bot/me/inbox                — забрать недоставленные ответы ИИ и менеджера

Идентичность: telegram_id бот берёт из апдейта Telegram и подставляет сам — клиент его не
передаёт. Каждая ручка по обращению сверяет владельца, поэтому через бота нельзя открыть
чужой диалог (Ст. 4). ПДн наружу не уходят: телефоны и почта отдаются только владельцу
карточки в его же чате.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import col, func, select

from app import models as m
from app.db import session_scope
from app.routers.leads_api import require_bot_token
from core import ai_engine, handoff
from core.leads import LeadError, apply_lead, build_lead
from core.learning import log_event
from core.schemas import IncomingMessage, utcnow

router = APIRouter(prefix="/api/bot", tags=["bot"])

#: Канал бота в контексте пайплайна: диалоги бота видны в дашборде как Telegram.
CHANNEL = "telegram"
#: Тип уведомления менеджеру, когда клиент зовёт человека (интерфейс дашборда его знает).
HANDOFF_NOTIFY_TYPE = "HANDOFF_REQUEST"
#: Обращения, которые бот показывает как «в работе».
OPEN_STATUSES = ("ACTIVE", "HOLD", "HANDOFF")
STATUS_TITLES = {
    "ACTIVE": "в работе, отвечает ИИ",
    "HOLD": "ждёт менеджера",
    "HANDOFF": "ведёт менеджер",
    "RESOLVED": "закрыто",
    "LOST": "потеряно",
}

BotOnly = Depends(require_bot_token)


# --------------------------------------------------------------------------- модели запросов
class TelegramBody(BaseModel):
    """Идентификация: бот всегда присылает telegram_id из апдейта."""

    telegram_id: str = Field(min_length=1, max_length=32)


class ProfileBody(TelegramBody):
    """Профиль клиента: имя обязательно (иначе менеджер не знает, к кому обращаться)."""

    name: str = Field(min_length=2, max_length=120)
    company: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=64)


class MessageBody(TelegramBody):
    """Сообщение клиента в пайплайн. `display_name` нужен для авто-карточки нового клиента."""

    text: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None
    display_name: str | None = Field(default=None, max_length=120)
    username: str | None = Field(default=None, max_length=64)


# --------------------------------------------------------------------------- общие помощники
def _client_by_telegram(session: Any, telegram_id: str) -> m.Client | None:
    return session.exec(select(m.Client).where(m.Client.telegram_id == telegram_id.strip())).first()


def _require_client(session: Any, telegram_id: str) -> m.Client:
    client = _client_by_telegram(session, telegram_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Этот Telegram ещё не знаком CRM: заполните профиль")
    return client


def _ensure_client(session: Any, telegram_id: str, display_name: str | None, username: str | None) -> tuple[m.Client, bool]:
    """Находит карточку по telegram_id, а если человек пишет впервые — заводит её.

    Карточка заводится через тот же `apply_lead`, что и анкета: менеджер получает
    уведомление «заявка из бота», а клиент сразу виден в разделе «Клиенты» на сайте.
    """
    client = _client_by_telegram(session, telegram_id)
    if client is not None:
        return client, False
    name = (display_name or "").strip() or f"Клиент Telegram {telegram_id}"
    if len(name) < 2:  # имя из Telegram может быть одной буквой — тогда по имени не позвать
        name = f"Клиент Telegram {telegram_id}"
    try:
        lead = build_lead(name=name, telegram_id=telegram_id, telegram_username=username, source="telegram_bot")
    except LeadError as exc:  # сюда попадаем только при пустом telegram_id — значит, баг бота
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    report = apply_lead(lead, session)
    client = session.get(m.Client, report["client_id"])
    assert client is not None
    return client, True


def _card(session: Any, client: m.Client) -> dict[str, Any]:
    """Карточка для экрана «Профиль»: контакты отдаём владельцу — их он сам и заполнял."""
    appeals = session.exec(select(m.Conversation).where(m.Conversation.client_id == client.id)).all()
    open_count = sum(1 for c in appeals if c.status in OPEN_STATUSES)
    return {
        "id": client.id,
        "name": client.name,
        "company": client.company or "",
        "phone": client.phone or "",
        "email": client.email or "",
        "status": client.status,
        "created_at": client.created_at.isoformat(timespec="seconds"),
        "appeals_total": len(appeals),
        "appeals_open": open_count,
    }


def _last_message(session: Any, conversation_id: int) -> m.Message | None:
    return session.exec(
        select(m.Message).where(m.Message.conversation_id == conversation_id).order_by(col(m.Message.id).desc())
    ).first()


def _appeal_row(session: Any, conv: m.Conversation) -> dict[str, Any]:
    """Строка списка «Мои обращения»: статус, тема и последняя реплика (обрезанная)."""
    last = _last_message(session, conv.id or 0)
    count = session.exec(
        select(func.count()).select_from(m.Message).where(m.Message.conversation_id == conv.id)
    ).one()
    return {
        "id": conv.id,
        "status": conv.status,
        "status_title": STATUS_TITLES.get(conv.status, conv.status),
        "category": conv.category or "",
        "channel": conv.channel,
        "created_at": conv.created_at.isoformat(timespec="seconds"),
        "updated_at": conv.updated_at.isoformat(timespec="seconds"),
        "last_text": (last.text[:160] if last else ""),
        "last_role": (last.role if last else ""),
        "messages": int(count),
        "waiting_manager": conv.status in ("HOLD", "HANDOFF"),
    }


def _conversation_of(session: Any, client_id: int, conversation_id: int) -> m.Conversation:
    """Обращение клиента. Чужой диалог не отдаём: это ошибка доступа, а не «не найдено»."""
    conv = session.get(m.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Обращение не найдено")
    if conv.client_id != client_id:
        raise HTTPException(status_code=403, detail="Это обращение другого клиента")
    return conv


def _messages(session: Any, conversation_id: int, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    rows = session.exec(
        select(m.Message)
        .where(m.Message.conversation_id == conversation_id, col(m.Message.id) > after)
        .order_by(col(m.Message.id))
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "role": row.role,
            "text": row.text,
            "phase": row.phase or "",
            "ts": row.ts.isoformat(timespec="seconds"),
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- профиль
@router.get("/me", dependencies=[BotOnly])
def get_me(telegram_id: str) -> dict[str, Any]:
    """Карточка клиента по telegram_id. `found=false` — бот предложит заполнить профиль."""
    with session_scope() as s:
        client = _client_by_telegram(s, telegram_id)
        if client is None:
            return {"found": False, "client": None}
        return {"found": True, "client": _card(s, client)}


@router.post("/me", dependencies=[BotOnly])
def save_me(body: ProfileBody) -> dict[str, Any]:
    """Сохраняет профиль из бота: обновляет только изменившиеся поля, менеджера уведомляет."""
    with session_scope() as s:
        try:
            lead = build_lead(
                name=body.name,
                company=body.company,
                phone=body.phone,
                email=body.email,
                telegram_id=body.telegram_id,
                telegram_username=body.username,
                source="telegram_bot",
                # профиль из бота — это ещё и контакт: без телефона или почты менеджер не позвонит
            )
        except LeadError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        report = apply_lead(lead, s)
        client = s.get(m.Client, report["client_id"])
        assert client is not None
        return {
            "ok": True,
            "created": report.get("created", False),
            "changed": report.get("changed", []),
            "client": _card(s, client),
        }


def _mark_delivered(conversation_id: int | None, texts: list[str]) -> None:
    """Помечает выданные сразу ответы как доставленные: бот уже отправил их в чат.

    Иначе клиент получил бы один и тот же текст дважды: бот отвечает на сообщение и тут же
    забирает тот же ответ из очереди (`/api/bot/me/inbox`). Финальные ответы, которые пайплайн
    посчитал уже после INTERIM, остаются в очереди и доедут отдельным сообщением.
    """
    if conversation_id is None or not texts:
        return
    with session_scope() as s:
        for text in texts:
            row = s.exec(
                select(m.Outbox)
                .where(
                    m.Outbox.conversation_id == conversation_id,
                    m.Outbox.delivered == False,  # noqa: E712 — иначе SQL-сравнение с NULL
                    m.Outbox.text == text,
                )
                .order_by(col(m.Outbox.id).desc())
            ).first()
            if row is not None:
                row.delivered = True


# --------------------------------------------------------------------------- обращения
@router.get("/me/appeals", dependencies=[BotOnly])
def list_appeals(telegram_id: str, open_only: bool = False) -> dict[str, Any]:
    """Мои обращения, свежие сверху. `open_only=true` — только те, что ещё в работе."""
    with session_scope() as s:
        client = _require_client(s, telegram_id)
        stmt = select(m.Conversation).where(m.Conversation.client_id == client.id)
        if open_only:
            stmt = stmt.where(col(m.Conversation.status).in_(OPEN_STATUSES))
        rows = s.exec(stmt.order_by(col(m.Conversation.updated_at).desc()).limit(50)).all()
        return {"appeals": [_appeal_row(s, conv) for conv in rows]}


@router.get("/me/appeals/{conversation_id}", dependencies=[BotOnly])
def get_appeal(telegram_id: str, conversation_id: int, after: int = 0) -> dict[str, Any]:
    """Обращение и его переписка. `after` — id последнего известного сообщения (для поллинга)."""
    with session_scope() as s:
        client = _require_client(s, telegram_id)
        conv = _conversation_of(s, client.id or 0, conversation_id)
        return {"appeal": _appeal_row(s, conv), "messages": _messages(s, conversation_id, after)}


@router.post("/me/appeals/{conversation_id}/manager", dependencies=[BotOnly])
def call_manager(body: TelegramBody, conversation_id: int, reason: str = "") -> dict[str, Any]:
    """Клиент просит живого человека: диалог уходит в HOLD, менеджер получает саммари.

    Тот же путь, что у спора: ИИ замолкает, в ленте менеджера появляется уведомление
    `HANDOFF_REQUEST`, а цикл HOLD напоминает о диалоге, пока его не возьмут.
    """
    with session_scope() as s:
        client = _require_client(s, body.telegram_id)
        conv = _conversation_of(s, client.id or 0, conversation_id)
        if conv.status == "RESOLVED":
            raise HTTPException(status_code=409, detail="Обращение уже закрыто — откройте новое")
        if conv.status in ("HOLD", "HANDOFF"):
            text = (
                "Менеджер уже подключён к этому обращению — отвечу здесь, как только он освободится."
                if conv.status == "HANDOFF"
                else "Менеджер уже уведомлён об этом обращении и ответит здесь, в Telegram."
            )
            return {"ok": True, "already": True, "status": conv.status, "text": text}

        handoff.refresh_handoff(s, conversation_id)  # менеджер получает саммари, а не «просто диалог»
        conv.status, conv.updated_at = "HOLD", utcnow()
        text = (
            "Передал обращение менеджеру — он ответит здесь, в Telegram."
            + (f" Ваш комментарий: «{reason.strip()[:200]}»." if reason.strip() else "")
        )
        s.add(m.Notification(type=HANDOFF_NOTIFY_TYPE, conversation_id=conversation_id, text=text))
        s.add(m.Message(conversation_id=conversation_id, role="ai", text=text, phase="HOLD", deterministic=True))
        log_event(s, "manager_requested", conversation_id, {"source": CHANNEL})
        return {"ok": True, "already": False, "status": "HOLD", "text": text}


@router.post("/me/appeals/{conversation_id}/close", dependencies=[BotOnly])
def close_appeal(body: TelegramBody, conversation_id: int) -> dict[str, Any]:
    """Клиент закрыл обращение: статус RESOLVED, уведомления и handoff гасятся."""
    with session_scope() as s:
        client = _require_client(s, body.telegram_id)
        conv = _conversation_of(s, client.id or 0, conversation_id)
        if conv.status == "RESOLVED":
            return {"ok": True, "already": True, "status": "RESOLVED", "text": "Обращение уже закрыто."}
        text = "Обращение закрыто. Понадобится помощь — просто напишите или нажмите «🆕 Обращение»."
        s.add(m.Message(conversation_id=conversation_id, role="ai", text=text, phase="FINAL", deterministic=True))
    handoff.resolve(conversation_id)
    return {"ok": True, "already": False, "status": "RESOLVED", "text": text}


# --------------------------------------------------------------------------- переписка
@router.post("/messages", dependencies=[BotOnly])
async def post_message(body: MessageBody) -> dict[str, Any]:
    """Сообщение клиента из Telegram → пайплайн (PERCEPTION → … → ACTION), как у канала web."""
    with session_scope() as s:
        client, created = _ensure_client(s, body.telegram_id, body.display_name, body.username)
        client_id = client.id or 0
    try:
        response = await ai_engine.process_message(
            IncomingMessage(
                client_id=client_id,
                channel=CHANNEL,
                text=body.text,
                conversation_id=body.conversation_id,
            )
        )
    except ValueError as exc:  # диалог не найден или принадлежит другому клиенту (Ст. 4)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _mark_delivered(response.conversation_id, [msg.text for msg in response.messages])
    return {
        "ok": True,
        "client_id": client_id,
        "created_client": created,
        "conversation_id": response.conversation_id,
        # в BotResponse лежат только ответы ассистента (BotMessage без роли) — роль добавляем,
        # чтобы бот мог отличить ответ ИИ от сообщения менеджера в одной ленте
        "messages": [{"role": "ai", **msg.model_dump()} for msg in response.messages],
    }


@router.post("/me/inbox", dependencies=[BotOnly])
def pull_inbox(body: TelegramBody, limit: int = 20) -> dict[str, Any]:
    """Недоставленные ответы ИИ и менеджера для клиента (доставка «at-least-once»).

    Бот забирает их из своего фасада, а не из `/api/outbox`: так каналу не нужен
    админ-токен, чтобы найти telegram_id владельца диалога.
    """
    with session_scope() as s:
        client = _require_client(s, body.telegram_id)
        conv_ids = [
            row.id
            for row in s.exec(select(m.Conversation).where(m.Conversation.client_id == client.id)).all()
        ]
        if not conv_ids:
            return {"items": []}
        rows = s.exec(
            select(m.Outbox)
            .where(
                m.Outbox.channel == CHANNEL,
                m.Outbox.delivered == False,  # noqa: E712 — сравнение с NULL-безопасным False нужно SQL
                col(m.Outbox.conversation_id).in_(conv_ids),
            )
            .order_by(col(m.Outbox.id))
            .limit(max(1, min(limit, 100)))
        ).all()
        items = []
        for row in rows:
            row.delivered = True
            # роль нужна боту, чтобы отличить ответ менеджера от догоняющего ответа ИИ
            # (в outbox роли нет — ищем сообщение с тем же текстом в том же диалоге)
            twin = s.exec(
                select(m.Message)
                .where(m.Message.conversation_id == row.conversation_id, m.Message.text == row.text)
                .order_by(col(m.Message.id).desc())
            ).first()
            items.append(
                {
                    "id": row.id,
                    "conversation_id": row.conversation_id,
                    "text": row.text,
                    "role": twin.role if twin else "",
                    "phase": row.phase or "",
                    "ts": row.ts.isoformat(timespec="seconds"),
                }
            )
        return {"items": items}
