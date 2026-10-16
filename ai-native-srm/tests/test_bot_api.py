"""Тесты фасада бота `/api/bot/*`: профиль, обращения, переписка, доставка.

Проверяем то, на что опирается Telegram-бот: токен `X-Bot-Token`, автосоздание карточки
при первом обращении, доступ только к своим диалогам, HOLD при вызове менеджера и выдачу
недоставленных сообщений. Сеть и модель не нужны: LLM_PROVIDER=mock.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from sqlmodel import select  # noqa: E402

from app import models as m  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import bot_api  # noqa: E402

TOKEN = "bot-secret"
HEAD = {"X-Bot-Token": TOKEN}
CLIENT_A = "111222333"
CLIENT_B = "999888777"


@pytest.fixture()
def api(db, monkeypatch):
    """Клиент API с поднятым lifespan и заданным сервисным токеном бота."""
    monkeypatch.setenv("BOT_API_TOKEN", TOKEN)
    monkeypatch.setattr(bot_api, "CHANNEL", "telegram")
    with TestClient(app) as client:
        yield client


def register(api: TestClient, telegram_id: str = CLIENT_A, name: str = "Дмитрий Соколов") -> dict:
    resp = api.post("/api/bot/me", headers=HEAD, json={"telegram_id": telegram_id, "name": name, "phone": "+7 999 123-45-67"})
    assert resp.status_code == 200, resp.text
    return resp.json()


def appeal_id(api: TestClient, telegram_id: str = CLIENT_A) -> int:
    rows = api.get("/api/bot/me/appeals", headers=HEAD, params={"telegram_id": telegram_id}).json()["appeals"]
    assert rows
    return rows[0]["id"]


def send_text(api: TestClient, text: str, telegram_id: str = CLIENT_A, conversation_id: int | None = None) -> dict:
    resp = api.post(
        "/api/bot/messages",
        headers=HEAD,
        json={"telegram_id": telegram_id, "text": text, "conversation_id": conversation_id, "display_name": "Дмитрий"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def wait_ai_answer(api: TestClient, conversation_id: int, telegram_id: str = CLIENT_A, tries: int = 20) -> list[dict]:
    """Пайплайн может ответить через outbox (после INTERIM) — ждём ответ ИИ в переписке."""
    for _ in range(tries):
        messages = api.get(
            f"/api/bot/me/appeals/{conversation_id}", headers=HEAD, params={"telegram_id": telegram_id}
        ).json()["messages"]
        if any(msg["role"] == "ai" for msg in messages):
            return messages
        time.sleep(0.1)
    raise AssertionError("ответ ИИ так и не появился в переписке")


# --------------------------------------------------------------------------- доступ
def test_token_is_required_and_explains_why(db, monkeypatch):
    monkeypatch.setenv("BOT_API_TOKEN", TOKEN)
    with TestClient(app) as client:
        assert client.get("/api/bot/me", params={"telegram_id": CLIENT_A}).status_code == 401
        assert client.get("/api/bot/me", headers={"X-Bot-Token": "wrong"}, params={"telegram_id": CLIENT_A}).status_code == 401

    monkeypatch.delenv("BOT_API_TOKEN", raising=False)  # настройки читаются из окружения при каждом запросе
    with TestClient(app) as client:
        resp = client.get("/api/bot/me", headers=HEAD, params={"telegram_id": CLIENT_A})
        assert resp.status_code == 503 and "BOT_API_TOKEN" in resp.json()["detail"]


# --------------------------------------------------------------------------- профиль
def test_profile_unknown_then_created(api):
    assert api.get("/api/bot/me", headers=HEAD, params={"telegram_id": CLIENT_A}).json() == {"found": False, "client": None}

    created = register(api)
    assert created["created"] is True and created["client"]["name"] == "Дмитрий Соколов"

    found = api.get("/api/bot/me", headers=HEAD, params={"telegram_id": CLIENT_A}).json()
    assert found["found"] is True
    assert found["client"]["phone"] == "+79991234567"  # телефон пришёл нормализованным
    assert found["client"]["appeals_total"] == 0


def test_profile_update_reports_changed_fields(api):
    register(api)
    updated = api.post(
        "/api/bot/me", headers=HEAD, json={"telegram_id": CLIENT_A, "name": "Дмитрий Соколов", "company": "ООО «Ромашка»"}
    ).json()
    assert updated["created"] is False and updated["changed"] == ["company"]
    assert updated["client"]["company"] == "ООО «Ромашка»"


def test_profile_validation_rejects_short_name(api):
    resp = api.post("/api/bot/me", headers=HEAD, json={"telegram_id": CLIENT_A, "name": "Д"})
    assert resp.status_code == 422


def test_profile_save_notifies_manager(api):
    register(api)
    with bot_api.session_scope() as s:
        types = [row.type for row in s.exec(select(m.Notification)).all()]
    assert "LEAD" in types  # заявка из бота попадает в ленту менеджера


# --------------------------------------------------------------------------- переписка
def test_message_creates_client_and_returns_ai_answer(api):
    resp = send_text(api, "Здравствуйте, есть ли PX-M4200?")
    assert resp["created_client"] is True and resp["conversation_id"]

    messages = wait_ai_answer(api, resp["conversation_id"])
    assert messages[0]["role"] == "client" and messages[0]["text"].startswith("Здравствуйте")
    assert messages[-1]["role"] == "ai" and messages[-1]["text"]


def test_second_message_continues_same_conversation(api):
    first = send_text(api, "Есть ли PX-M4200?")
    await_answer = wait_ai_answer(api, first["conversation_id"])
    assert await_answer

    second = send_text(api, "А сколько стоит?", conversation_id=first["conversation_id"])
    assert second["conversation_id"] == first["conversation_id"]

    appeals = api.get("/api/bot/me/appeals", headers=HEAD, params={"telegram_id": CLIENT_A}).json()["appeals"]
    assert len(appeals) == 1 and appeals[0]["messages"] >= 3


def test_answer_sent_inline_is_not_queued_for_delivery(api):
    """Ответ, который бот уже показал, не должен приехать второй раз из очереди."""
    resp = send_text(api, "Есть ли PX-M4200 в наличии?")
    shown = {msg["text"] for msg in resp["messages"] if msg.get("text")}
    assert shown

    queued = api.post("/api/bot/me/inbox", headers=HEAD, json={"telegram_id": CLIENT_A}).json()["items"]
    assert not (shown & {item["text"] for item in queued}), "ответ ИИ доставляется клиенту дважды"


def test_message_to_foreign_conversation_is_rejected(api):
    """Чужой диалог через бота не открыть: сервер сверяет владельца (Ст. 4)."""
    mine = send_text(api, "Нужна консультация по МФУ")
    conversation_id = mine["conversation_id"]
    register(api, CLIENT_B, name="Другой Клиент")

    resp = api.post(
        "/api/bot/messages",
        headers=HEAD,
        json={"telegram_id": CLIENT_B, "text": "подглядываю", "conversation_id": conversation_id},
    )
    assert resp.status_code == 404  # для чужого клиента диалога не существует

    detail = api.get(f"/api/bot/me/appeals/{conversation_id}", headers=HEAD, params={"telegram_id": CLIENT_B})
    assert detail.status_code == 403


# --------------------------------------------------------------------------- обращения
def test_appeals_list_and_detail(api):
    mine = send_text(api, "Вопрос по доставке в Казань")
    wait_ai_answer(api, mine["conversation_id"])

    rows = api.get("/api/bot/me/appeals", headers=HEAD, params={"telegram_id": CLIENT_A}).json()["appeals"]
    assert len(rows) == 1 and rows[0]["id"] == mine["conversation_id"]
    assert rows[0]["status"] in ("ACTIVE", "HOLD", "HANDOFF") and rows[0]["messages"] >= 1
    assert rows[0]["last_text"]

    detail = api.get(
        f"/api/bot/me/appeals/{mine['conversation_id']}", headers=HEAD, params={"telegram_id": CLIENT_A}
    ).json()
    assert detail["appeal"]["id"] == mine["conversation_id"] and detail["messages"]


def test_appeals_require_known_client(api):
    resp = api.get("/api/bot/me/appeals", headers=HEAD, params={"telegram_id": "нет-такого"})
    assert resp.status_code == 404 and "профиль" in resp.json()["detail"].lower()


def test_manager_call_sets_hold_and_notifies(api):
    register(api)
    conversation_id = send_text(api, "Принтер пришёл с браком, нужна замена")["conversation_id"]

    resp = api.post(
        f"/api/bot/me/appeals/{conversation_id}/manager",
        headers=HEAD,
        json={"telegram_id": CLIENT_A},
        params={"reason": "Нужен человек"},
    )
    assert resp.status_code == 200 and resp.json()["already"] is False
    assert "менеджеру" in resp.json()["text"]

    detail = api.get(f"/api/bot/me/appeals/{conversation_id}", headers=HEAD, params={"telegram_id": CLIENT_A}).json()
    assert detail["appeal"]["status"] == "HOLD" and detail["appeal"]["waiting_manager"] is True

    with bot_api.session_scope() as s:
        notes = [row.type for row in s.exec(select(m.Notification)).all()]
        handoff_rows = list(s.exec(select(m.Handoff)).all())
    assert "HANDOFF_REQUEST" in notes  # менеджер видит запрос в ленте дашборда
    assert handoff_rows  # саммари готово до подключения менеджера

    again = api.post(f"/api/bot/me/appeals/{conversation_id}/manager", headers=HEAD, json={"telegram_id": CLIENT_A}).json()
    assert again["already"] is True  # повторный вызов не плодит уведомления


def test_close_appeal_from_bot(api):
    register(api)
    conversation_id = send_text(api, "Вопрос по гарантии")["conversation_id"]

    resp = api.post(f"/api/bot/me/appeals/{conversation_id}/close", headers=HEAD, json={"telegram_id": CLIENT_A})
    assert resp.status_code == 200 and resp.json()["status"] == "RESOLVED"

    detail = api.get(f"/api/bot/me/appeals/{conversation_id}", headers=HEAD, params={"telegram_id": CLIENT_A}).json()
    assert detail["appeal"]["status"] == "RESOLVED"

    manager = api.post(f"/api/bot/me/appeals/{conversation_id}/manager", headers=HEAD, json={"telegram_id": CLIENT_A})
    assert manager.status_code == 409  # закрытое обращение менеджеру не отдаём


# --------------------------------------------------------------------------- доставка клиенту
def test_inbox_delivers_manager_reply_once(api):
    register(api)
    conversation_id = send_text(api, "Когда будет замена?")["conversation_id"]
    wait_ai_answer(api, conversation_id)
    api.post("/api/bot/me/inbox", headers=HEAD, json={"telegram_id": CLIENT_A})  # добираем ответ ИИ

    api.post(
        f"/api/conversations/{conversation_id}/manager-reply",
        json={"manager": "Ирина", "text": "Замену согласовал, курьер в пятницу"},
    )

    items = api.post("/api/bot/me/inbox", headers=HEAD, json={"telegram_id": CLIENT_A}).json()["items"]
    assert [item["text"] for item in items] == ["Замену согласовал, курьер в пятницу"]
    assert items[0]["role"] == "manager"  # бот покажет сообщение как ответ человека

    assert api.post("/api/bot/me/inbox", headers=HEAD, json={"telegram_id": CLIENT_A}).json()["items"] == []
