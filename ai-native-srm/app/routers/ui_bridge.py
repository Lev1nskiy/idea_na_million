"""REST-мост для сайта-фронтенда «ЛОСЬ»: `/api/site/*` (Шаг 7, интеграция с коллегой).

Зачем отдельная поверхность, а не правка существующего API:
* свой API (`/api/...`) уже используют Streamlit-UI, Telegram-адаптер, админка и 269 тестов —
  ломать их контракт ради UI сайта нельзя;
* сайт написан под свой REST-контракт (camelCase, строковые id `dlg-0001`, обёртки
  `{conversations: [...]}`, Policy в форме с `work_hours.from/to`, метрики своей нарезки).
  Поэтому сайт ходит на `/api/site/*`, а перевод форм живёт в `app/adapters/site.py`.

Что важно: это не второй источник истины и не второй движок. Все данные приходят из той же
SQLite и того же пайплайна (`core.ai_engine`), бизнес-логики в мосте нет — только адаптация
и вызовы существующих use-case-функций (`core.handoff`, `core.policy`, `core.ai_engine`).

Схема подключения сайта: их Next.js-роуты (`frnt/src/app/api/**`) проксируют запросы сюда,
используя `CRM_BACKEND_URL`; если бэкенд недоступен, они откатываются на свой внутрипроцессный
mock — демо продолжает жить автономно (см. `docs/frontend_integration.md`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel import col, select, text

from app import models as m
from app.adapters import site, site_metrics
from app.db import session_scope
from core import ai_engine, auth
from core.llm import describe_llm
from core import handoff as handoff_core
from core import tools
from core.learning import log_event
from core.policy import (
    calc_allowed_discount,
    calc_delivery_compensation,
    get_policy,
    get_policy_version,
    list_policy_versions,
    update_policy,
)
from core.schemas import IncomingMessage, utcnow

# Публичный минимум (health до входа в систему) и защищённая часть: всё остальное
# доступно только сотруднику с живой сессией. Политика и сброс демо — исключительно
# администратору; права проверяются на бэкенде (core/auth.py), интерфейс лишь прячет кнопки.
public_router = APIRouter(prefix="/api/site", tags=["site"])
router = APIRouter(prefix="/api/site", tags=["site"], dependencies=[Depends(auth.current_user)])
admin_only = [Depends(auth.admin_user)]

DEFAULT_MANAGER = "Менеджер А."  # как в mock сайта: различимо в интерфейсе менеджера


class ManagerBody(BaseModel):
    """Кто подключается к диалогу (сайт может не присылать — тогда менеджер по умолчанию)."""

    manager: str | None = None


class ManagerReplyBody(BaseModel):
    """Ответ менеджера клиенту."""

    text: str
    manager: str | None = None


class OverrideBody(BaseModel):
    """Переопределение авторешения: причина обязательна по смыслу, но не по контракту сайта."""

    reason: str | None = None
    by: str | None = None


# ---------------------------------------------------------------------------
# Служебное
# ---------------------------------------------------------------------------
@public_router.get("/health")
def health() -> dict[str, Any]:
    """Легковесный healthcheck сайта: бэкенд-движок работает даже без БД (degraded)."""
    db_state, degraded = "up", False
    try:
        with session_scope() as s:
            s.exec(text("select 1"))
    except Exception:  # noqa: BLE001 — health не должен падать из-за БД
        db_state, degraded = "down", True
    body: dict[str, Any] = {
        "ok": True,
        "service": "los-ai-crm",
        "db": db_state,
        "time": site.iso(utcnow()),
        # Какая LLM отвечает клиентам и чего не хватает остальным провайдерам —
        # ровно те же имена переменных, что и в docs/llm_setup.md. Ключей здесь нет.
        **describe_llm(),
    }
    if degraded:
        body["degraded"] = True
        body["note"] = "API отвечает; политика и диалоги читаются из остальных таблиц"
    return body


@router.get("/clients")
def clients() -> dict[str, Any]:
    """Клиенты для селектора в чате и карточек дашборда (замена статического CLIENT_OPTIONS).

    Источник — общий справочник демо-данных (`data/clients_history.json`), дополненный
    строками из БД: селектор работает даже до того, как по клиенту был первый диалог.
    """
    with session_scope() as s:
        db_rows = {row.id: row for row in s.exec(select(m.Client)).all()}
    items: list[dict[str, Any]] = []
    for client_id, source in sorted(tools._clients().items()):  # noqa: SLF001 — справочник как есть
        row = db_rows.pop(client_id, None)
        items.append(
            site.client_option(
                row or _ClientView(client_id, source),
                source,
            )
        )
    for row in db_rows.values():  # клиенты, которых нет в справочнике (созданы из чата)
        items.append(site.client_option(row, None))
    items.sort(key=lambda item: item["id"])  # порядок селектора сайта: C-1001…C-1006
    return {"clients": items}


# ---------------------------------------------------------------------------
# Диалоги
# ---------------------------------------------------------------------------
def _conversations_payload(status: str | None) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = list(s.exec(select(m.Conversation).order_by(col(m.Conversation.id))).all())
        messages = list(s.exec(select(m.Message)).all())

    by_conv: dict[int, list[m.Message]] = {}
    for msg in messages:
        by_conv.setdefault(msg.conversation_id, []).append(msg)

    items: list[dict[str, Any]] = []
    clients_cache: dict[int, dict[str, Any] | None] = {}
    for conv in rows:
        own = sorted(by_conv.get(conv.id, []), key=lambda x: (x.ts, x.id or 0))
        payload = site.conversation_out(
            conv,
            last_message=own[-1].text if own else None,
            last_at=own[-1].ts if own else None,
            message_count=len(own),
        )
        if status and payload["status"] != status:
            continue
        if conv.client_id not in clients_cache:
            clients_cache[conv.client_id] = site.client_record(conv.client_id)
        payload["client"] = clients_cache[conv.client_id]
        items.append(payload)
    items.sort(key=lambda c: c["lastAt"], reverse=True)
    return items


@router.get("/conversations")
def conversations(status: str | None = Query(default=None, description="ACTIVE | HOLD | HANDOFF | RESOLVED")) -> dict[str, Any]:
    return {"conversations": _conversations_payload(status)}


@router.get("/conversations/{conv_id}")
def conversation_detail(conv_id: str) -> dict[str, Any]:
    """Диалог целиком: карточка клиента, лента, саммари менеджеру, решения и сделка."""
    conv_pk = _pk(conv_id)
    with session_scope() as s:
        conv = s.get(m.Conversation, conv_pk)
        if conv is None:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        own = sorted(
            s.exec(select(m.Message).where(m.Message.conversation_id == conv_pk)).all(),
            key=lambda x: (x.ts, x.id or 0),
        )
        handoff_row = s.exec(
            select(m.Handoff).where(m.Handoff.conversation_id == conv_pk).order_by(col(m.Handoff.id).desc())
        ).first()
        decisions = list(
            s.exec(select(m.Decision).where(m.Decision.conversation_id == conv_pk).order_by(col(m.Decision.id))).all()
        )
        deal = s.exec(
            select(m.Deal)
            .where(m.Deal.conversation_id == conv_pk)
            .order_by(col(m.Deal.id).desc())
        ).first() or s.exec(
            select(m.Deal).where(m.Deal.client_id == conv.client_id).order_by(col(m.Deal.id).desc())
        ).first()

    category_by_message = {d.message_id: d.category for d in decisions if d.message_id}
    item = site.conversation_out(
        conv, last_message=own[-1].text if own else None, last_at=own[-1].ts if own else None, message_count=len(own)
    )
    ext_id = site.out_id("dlg", conv_pk)
    return {
        "conversation": {k: v for k, v in item.items() if k not in ("lastMessage", "lastAt", "messageCount", "waitingMinutes")},
        "client": site.client_record(conv.client_id) or _client_from_db(conv.client_id),
        "messages": [site.message_out(row, ext_id, category_by_message.get(row.id)) for row in own],
        "handoff": site.handoff_out(handoff_row) if handoff_row else None,
        "decisions": [site.decision_out(row) for row in decisions],
        "deal": site.deal_out(deal) if deal else None,
    }


@router.get("/conversations/{conv_id}/messages")
def conversation_messages(conv_id: str, after: str | None = Query(default=None)) -> dict[str, Any]:
    """Лента диалога; `after` — либо ISO-время последнего показанного сообщения, либо `m-0012`."""
    conv_pk = _pk(conv_id)
    after_ts, after_id = _after_cursor(after)
    with session_scope() as s:
        if s.get(m.Conversation, conv_pk) is None:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        rows = list(
            s.exec(select(m.Message).where(m.Message.conversation_id == conv_pk).order_by(col(m.Message.id))).all()
        )
        decisions = list(s.exec(select(m.Decision).where(m.Decision.conversation_id == conv_pk)).all())
    category_by_message = {d.message_id: d.category for d in decisions if d.message_id}
    ext_id = site.out_id("dlg", conv_pk)
    items = [
        site.message_out(row, ext_id, category_by_message.get(row.id))
        for row in rows
        if (after_ts is None or row.ts > after_ts) and row.id > after_id
    ]
    return {"messages": items}


@router.post("/conversations/{conv_id}/takeover")
def takeover(conv_id: str, body: ManagerBody | None = None) -> dict[str, Any]:
    """Менеджер подключается к спору: HANDOFF, напоминания прекращаются, клиент получает сигнал."""
    conv_pk = _pk(conv_id)
    manager = (body.manager if body else None) or DEFAULT_MANAGER
    try:
        summary = handoff_core.takeover(conv_pk, manager)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    with session_scope() as s:
        conv = s.get(m.Conversation, conv_pk)
        assert conv is not None
        _store_system_message(s, conv, "К диалогу подключился менеджер. ИИ больше не отвечает клиенту.")
    row = _handoff_row(conv_pk)
    return {
        "ok": True,
        "status": "HANDOFF",
        "manager": manager,
        "summary": site.handoff_out(row)["summary"] if row else None,
    }


@router.post("/conversations/{conv_id}/manager-reply")
def manager_reply(conv_id: str, body: ManagerReplyBody) -> dict[str, Any]:
    """Ответ менеджера клиенту: в ленту, в outbox канала; ИИ в этом диалоге замолкает."""
    conv_pk = _pk(conv_id)
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=400, detail="text обязателен")
    try:
        # Ответ менеджера в HOLD = автоматический takeover (как в mock сайта).
        handoff_core.manager_reply(conv_pk, body.manager or DEFAULT_MANAGER, body.text.strip())
        with session_scope() as s:
            conv = s.get(m.Conversation, conv_pk)
            assert conv is not None
            if conv.status == "HOLD":
                handoff_core.takeover(conv_pk, body.manager or DEFAULT_MANAGER)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/conversations/{conv_id}/resolve")
def resolve(conv_id: str) -> dict[str, Any]:
    """Диалог закрыт (RESOLVED): уведомления гасятся, Handoff закрывается."""
    conv_pk = _pk(conv_id)
    try:
        handoff_core.resolve(conv_pk)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    with session_scope() as s:
        conv = s.get(m.Conversation, conv_pk)
        assert conv is not None
        _store_system_message(s, conv, "Диалог закрыт. Настроение клиента стабилизировано.")
    return {"ok": True, "status": "RESOLVED"}


# ---------------------------------------------------------------------------
# Сообщение клиента (основной вход пайплайна)
# ---------------------------------------------------------------------------
class IncomingBody(BaseModel):
    """Тело запроса сайта: `{client_id: "C-1005", channel: "web", text: "..."}`."""

    client_id: str
    text: str
    channel: str = "web"


@router.post("/messages")
async def post_message(body: IncomingBody) -> dict[str, Any]:
    """Пропускает сообщение клиента через наш пайплайн и отдаёт ответ + трассировку.

    Возвращаем сообщения УЖЕ сохранённые (с id из БД): сайт добавляет их в ленту по id,
    поэтому дубликаты при следующем поллинге не появляются.
    """
    if not body.text or not body.text.strip():
        raise HTTPException(status_code=400, detail="client_id и text обязательны")
    try:
        client_pk = _client_pk(body.client_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    channel = body.channel if body.channel in ("web", "telegram") else "web"

    with session_scope() as s:
        _ensure_client_row(s, client_pk)
        last_id = s.exec(select(m.Message.id).order_by(col(m.Message.id).desc())).first() or 0

    response = await ai_engine.process_message(
        IncomingMessage(client_id=client_pk, channel=channel, text=body.text.strip())  # type: ignore[arg-type]
    )
    conv_pk = response.conversation_id
    if conv_pk is None:
        return {"conversationId": None, "messages": [], "trace": site.trace_out(response.trace)}

    with session_scope() as s:
        fresh = list(
            s.exec(
                select(m.Message)
                .where(m.Message.conversation_id == conv_pk, m.Message.role != "client")
                .order_by(col(m.Message.id))
            ).all()
        )
        own = [row for row in fresh if (row.id or 0) > (last_id or 0)]
        decisions = list(s.exec(select(m.Decision).where(m.Decision.conversation_id == conv_pk)).all())
    category_by_message = {d.message_id: d.category for d in decisions if d.message_id}
    ext_id = site.out_id("dlg", conv_pk)
    return {
        "conversationId": ext_id,
        "messages": [site.message_out(row, ext_id, category_by_message.get(row.id)) for row in own],
        "trace": site.trace_out(response.trace),
    }


# ---------------------------------------------------------------------------
# Сделки, решения, уведомления, инциденты
# ---------------------------------------------------------------------------
@router.get("/deals")
def deals() -> dict[str, Any]:
    with session_scope() as s:
        rows = list(s.exec(select(m.Deal).order_by(col(m.Deal.id).desc())).all())
    return {"deals": [site.deal_out(row) for row in rows]}


@router.post("/decisions/{decision_id}/override")
def override_decision(decision_id: str, body: OverrideBody) -> dict[str, Any]:
    """Переопределение автономного решения человеком (Ст. 5): любая авто-операция отменяема."""
    dec_pk = _pk(decision_id)
    with session_scope() as s:
        row = s.get(m.Decision, dec_pk)
        if row is None:
            raise HTTPException(status_code=404, detail="Решение не найдено")
        row.overridden_by = body.by or DEFAULT_MANAGER
        row.override_reason = body.reason or "без указания причины"
        row.overridden_at = utcnow()
        log_event(s, "override", row.conversation_id, {"decision_id": dec_pk, "manager": row.overridden_by})
        return {"ok": True, "decision": site.decision_out(row)}


@router.get("/notifications")
def notifications() -> dict[str, Any]:
    with session_scope() as s:
        rows = list(s.exec(select(m.Notification).order_by(col(m.Notification.id).desc())).all())
        convs = {c.id: c for c in s.exec(select(m.Conversation)).all()}
        clients = {c.id: c.name for c in s.exec(select(m.Client)).all()}
    items = []
    for row in rows:
        conv = convs.get(row.conversation_id)
        items.append(
            site.notification_out(
                row,
                client_name=clients.get(conv.client_id, "—") if conv else "—",
                conv_status=conv.status if conv else None,
                conversation_ext_id=site.out_id("dlg", row.conversation_id) if row.conversation_id else "",
            )
        )
    items.sort(key=lambda n: (n["active"], n["lastNotifiedAt"]), reverse=True)
    return {"notifications": items, "unread": sum(1 for n in items if not n["read"] and n["active"])}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: str) -> dict[str, Any]:
    ntf_pk = _pk(notification_id)
    with session_scope() as s:
        row = s.get(m.Notification, ntf_pk)
        if row is None:
            raise HTTPException(status_code=404, detail="Уведомление не найдено")
        row.read = True
    return {"ok": True}


@router.get("/security/incidents")
def incidents(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """Инциденты prompt injection: только маскированный текст и хэш (Ст. 4)."""
    with session_scope() as s:
        rows = list(s.exec(select(m.SecurityIncident).order_by(col(m.SecurityIncident.id).desc()).limit(limit)).all())
        convs = {c.id: c for c in s.exec(select(m.Conversation)).all()}
        clients = {c.id: c.name for c in s.exec(select(m.Client)).all()}
    return {
        "incidents": [
            site.incident_out(
                row,
                client_name=clients.get(convs[row.conversation_id].client_id, "—")
                if row.conversation_id in convs
                else "—",
            )
            for row in rows
        ]
    }


# ---------------------------------------------------------------------------
# Policy Matrix, симулятор и метрики
# ---------------------------------------------------------------------------
@router.get("/policy", dependencies=admin_only)
def policy_get() -> dict[str, Any]:
    with session_scope() as s:
        policy = get_policy(s)
        version = list_policy_versions(limit=1, session=s)
    meta = version[0] if version else None
    return {"policy": site.policy_out(policy, author=meta.author if meta else "system", updated_at=meta.ts if meta else None)}


@router.put("/policy", dependencies=admin_only)
def policy_put(body: dict[str, Any]) -> dict[str, Any]:
    """Создаёт новую версию политики; вступает в силу немедленно (кэша нет)."""
    with session_scope() as s:
        current = get_policy(s)
    patch = site.policy_in(body)
    summary = _policy_summary(current, patch)
    policy = update_policy(patch, author=str(body.get("author") or "Администратор"), note=summary)
    with session_scope() as s:
        version = list_policy_versions(limit=1, session=s)
    meta = version[0] if version else None
    return {
        "ok": True,
        "policy": site.policy_out(policy, author=meta.author if meta else "Администратор", updated_at=meta.ts if meta else None),
        "summary": summary,
    }


@router.get("/policy/history", dependencies=admin_only)
def policy_history(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    with session_scope() as s:
        rows = list_policy_versions(limit=limit, session=s)
    return {
        "history": [
            {"version": row.version, "author": row.author, "at": site.iso(row.ts), "summary": row.note or ""}
            for row in rows
        ]
    }


class SimulateBody(BaseModel):
    """Аргументы симулятора на странице Policy: сумма/количество/сегмент/комплект/задержка."""

    sum: float = 0
    qty: int = 1
    segment: str = "std"
    isBundle: bool = False
    delayDays: int = 0
    orderSum: float | None = None


@router.post("/policy/simulate", dependencies=admin_only)
def policy_simulate(body: SimulateBody) -> dict[str, Any]:
    """Считает скидку и компенсацию ТЕМИ ЖЕ функциями, что и движок (`core.policy`).

    Mock сайта считал по своей копии формул; здесь источник — бэкенд, поэтому симулятор
    показывает ровно то, что выдаст клиенту пайплайн.
    """
    with session_scope() as s:
        policy = get_policy(s)
    status = site.status_from_segment(body.segment)
    pct = calc_allowed_discount(policy, body.qty, body.sum, status, body.isBundle)
    compensation = calc_delivery_compensation(policy, body.delayDays, body.orderSum or body.sum)
    discount = {"pct": pct, "reason": _discount_reason(policy, body.sum, status, body.isBundle, pct)}
    return {
        "policyVersion": policy.version,
        "discount": discount,
        "discountAmount": round(body.sum * pct / 100),
        "compensation": {"pct": compensation.pct, "amount": compensation.amount} if compensation.pct else None,
        "sla": site.sla_out(policy),
    }


@router.get("/metrics")
def metrics() -> dict[str, Any]:
    """Метрики в нарезке сайта + журналы событий и решений (страница LEARNING)."""
    with session_scope() as s:
        policy = get_policy(s)
        report = site_metrics.compute_site_metrics(s, policy)
        events = site_metrics.events_feed(s)
        decisions = site_metrics.decisions_feed(s)
        convs = {c.id: c for c in s.exec(select(m.Conversation)).all()}
        clients = {c.id: c.name for c in s.exec(select(m.Client)).all()}
    return {
        **report,
        "events": [site.event_out(e) for e in events],
        "decisions": [
            {**site.decision_out(d), "clientName": clients.get(convs[d.conversation_id].client_id, "—")
             if d.conversation_id in convs else "—"}
            for d in decisions
        ],
    }


# ---------------------------------------------------------------------------
# Демо: сброс к исходному состоянию
# ---------------------------------------------------------------------------
@router.post("/demo/reset", dependencies=admin_only)
def demo_reset() -> dict[str, Any]:
    """Сброс демо-стенда: пустые диалоги + клиенты/политика из справочников (как `seed_demo --reset`).

    Осторожно: ручка для демонстраций на защите, не для продакшна — стирает runtime-таблицы.
    """
    from scripts.seed_demo import seed  # локальный импорт: скрипт демо не нужен остальному API

    report = seed(reset=True)
    return {"ok": True, "report": report}


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------
def _pk(value: str) -> int:
    """Строковый id сайта (`dlg-0007`, `774`) → первичный ключ БД."""
    try:
        return site.parse_id(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Не найдено") from exc


def _client_pk(value: str) -> int:
    """Идентификатор клиента сайта (`C-1001`) → `clients.id` по канону демо-данных."""
    try:
        return site.client_pk_of(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _after_cursor(after: str | None) -> tuple[datetime | None, int]:
    """Разбирает курсор поллинга: ISO-время сайта либо `m-0012`."""
    if not after:
        return None, 0
    candidate = after.strip()
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")).replace(tzinfo=None), 0
    except ValueError:
        return None, site.parse_id(candidate)


def _ensure_client_row(session: Any, client_id: int) -> None:
    """Гарантирует существование клиента: сайт может прислать id, которого ещё нет в БД."""
    if session.get(m.Client, client_id) is not None:
        return
    source = tools.get_client(client_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Клиент C-{1000 + client_id} не найден")
    session.add(
        m.Client(
            id=client_id,
            name=source["name"],
            phone=source.get("phone"),
            email=source.get("email"),
            company=source.get("company"),
            status=source.get("status", "new"),
            telegram_id=source.get("telegram_id"),
        )
    )


class _ClientView:
    """Минимальный «снимок» клиента для селектора: данные берём из справочника, а не из БД."""

    def __init__(self, client_id: int, source: dict[str, Any]) -> None:
        self.id = client_id
        self.name = source["name"]
        self.company = source.get("company")
        self.status = source.get("status", "new")


def _client_from_db(client_id: int) -> dict[str, Any] | None:
    """Карточка клиента для диалогов, заведённых сайтом (нет в справочнике демо-данных)."""
    with session_scope() as s:
        row = s.get(m.Client, client_id)
    if row is None:
        return None
    return {
        "id": site.client_id_out(client_id),
        "name": row.name,
        "contact": "",
        "phone": row.phone or "",
        "email": row.email or "",
        "segment": site.segment_from_status(row.status),
        "note": "",
        "orders": [],
    }


def _handoff_row(conversation_id: int) -> Any:
    with session_scope() as s:
        return s.exec(
            select(m.Handoff)
            .where(m.Handoff.conversation_id == conversation_id)
            .order_by(col(m.Handoff.id).desc())
        ).first()


def _store_system_message(session: Any, conv: m.Conversation, text_: str) -> None:
    """Системная заметка в ленте (виден факт подключения менеджера/закрытия диалога)."""
    session.add(m.Message(conversation_id=conv.id, role="ai", text=text_, phase="FINAL", deterministic=True))
    session.add(m.Outbox(channel=conv.channel, conversation_id=conv.id, text=text_, phase="FINAL"))


def _policy_summary(current: Any, patch: dict[str, Any]) -> str:
    """Короткое описание изменений для истории версий (страница Policy → «История»)."""
    diffs: list[str] = []
    discounts = patch.get("discounts") or {}
    if "max_pct" in discounts and discounts["max_pct"] != current.discounts.max_pct:
        diffs.append(f"max_pct: {current.discounts.max_pct}% → {discounts['max_pct']}%")
    if "tiers" in discounts:
        diffs.append("обновлены тиры скидок")
    if "loyal_bonus_pct" in discounts and discounts["loyal_bonus_pct"] != current.discounts.loyal_bonus_pct:
        diffs.append(f"бонус лояльности: {discounts['loyal_bonus_pct']}%")
    warranty = patch.get("warranty") or {}
    if "replace_hours" in warranty and warranty["replace_hours"] != current.warranty.replace_hours:
        diffs.append(f"замена: {current.warranty.replace_hours}ч → {warranty['replace_hours']}ч")
    if "delivery_compensation" in patch:
        diffs.append("обновлены компенсации доставки")
    sla = patch.get("manager_sla") or {}
    if "response_hours" in sla and sla["response_hours"] != current.manager_sla.response_hours:
        diffs.append(f"SLA менеджера: {sla['response_hours']}ч")
    thresholds = patch.get("thresholds") or {}
    if "min_confidence" in thresholds and thresholds["min_confidence"] != current.thresholds.min_confidence:
        diffs.append(f"min_confidence → {thresholds['min_confidence']}")
    return "; ".join(diffs) or "редакция без изменения ключевых значений"


def _discount_reason(policy: Any, order_sum: float, status: str, is_bundle: bool, pct: float) -> str:
    """Пояснение к скидке для симулятора (повторяет логику `calc_allowed_discount`)."""
    parts: list[str] = []
    for tier in sorted(policy.discounts.tiers, key=lambda t: t.min_sum):
        if order_sum > tier.min_sum:
            parts.append(f"тир {tier.pct}% от суммы заказа")
    if status in policy.discounts.loyal_statuses:
        parts.append(f"бонус постоянного клиента +{policy.discounts.loyal_bonus_pct}%")
    if is_bundle:
        parts.append(f"комплект — не ниже {policy.discounts.bundle_max_pct}%")
    if pct >= policy.discounts.max_pct:
        parts.append(f"потолок матрицы {policy.discounts.max_pct}%")
    return " · ".join(parts) or "заказ не достиг тира скидки"


@router.get("/whoami")
def whoami(request: Request) -> dict[str, Any]:
    """Диагностика для прокси сайта: какой адрес бэкенда видит клиент (удобно при отладке)."""
    return {
        "base": str(request.base_url).rstrip("/"),
        "policy_version": get_policy_version(),
        "client_origin": request.headers.get("origin"),
    }
