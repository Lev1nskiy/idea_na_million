"""Заявки из бота-анкеты: нормализация анкеты, карточка клиента, ручки /api/leads."""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import select  # noqa: E402

from app import models as m  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.routers import leads_api  # noqa: E402
from core.leads import Lead, LeadError, apply_lead, build_lead, normalize_phone  # noqa: E402

TOKEN = {"X-Bot-Token": "bot-secret"}
QUESTION = {
    "name": "Дмитрий Соколов",
    "company": "ООО Ромашка",
    "phone": "8 (999) 123-45-67",
    "email": "Zakaz@Romashka.RU",
    "request": "10 принтеров LP-220, нужна скидка",
    "telegram_id": "555001",
    "telegram_username": "@dm_sokolov",
}


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setenv("BOT_API_TOKEN", "bot-secret")
    app = FastAPI()
    app.include_router(leads_api.router)
    return TestClient(app)


# --- Нормализация -----------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 (999) 123-45-67", "+79991234567"),
        ("+7 999 123 45 67", "+79991234567"),
        ("9991234567", "+79991234567"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_normalize_phone_variants(raw, expected):
    assert normalize_phone(raw) == expected


def test_normalize_phone_empty_stays_empty():
    assert normalize_phone(None) is None and normalize_phone("   ") is None


def test_build_lead_cleans_spaces_and_lowercases_email():
    lead = build_lead(**QUESTION, source="telegram_lead_bot")

    assert lead.name == "Дмитрий Соколов"
    assert lead.phone == "+79991234567"
    assert lead.email == "zakaz@romashka.ru"
    assert lead.telegram_username == "dm_sokolov"  # @ убираем
    assert lead.source == "telegram_lead_bot"


def test_build_lead_requires_name_and_contact():
    with pytest.raises(LeadError, match="Имя"):
        build_lead(name="Я")
    with pytest.raises(LeadError, match="контакт"):
        build_lead(name="Дмитрий Соколов")


def test_telegram_only_contact_allowed_when_explicit():
    """Клиент не дал телефон и почту — контактом становится сам Telegram, заявка не теряется."""
    lead = build_lead(name="Дмитрий", telegram_id=42, require_contacts=False)
    assert lead.telegram_id == "42" and lead.phone is None


def test_bad_email_is_rejected():
    with pytest.raises(LeadError, match="E-mail"):
        build_lead(name="Дмитрий", email="нет-собаки@почта")


def test_payload_for_logs_has_no_personal_data():
    payload = build_lead(**QUESTION).as_payload()
    assert payload["has_phone"] is True and payload["request_len"] > 0
    assert "phone" not in payload and "+79991234567" not in str(payload)


# --- Карточка клиента -------------------------------------------------------------------------

def test_apply_lead_creates_client_card_notification_and_event(db):
    report = apply_lead(build_lead(**QUESTION))

    assert report["created"] is True and report["client_id"]
    assert report["notification"].endswith(f"#{report['client_id']})")
    assert "+79991234567" not in report["notification"]  # контакты живут в карточке

    with session_scope() as session:
        client = session.get(m.Client, report["client_id"])
        assert client is not None
        assert (client.name, client.company, client.phone) == ("Дмитрий Соколов", "ООО Ромашка", "+79991234567")
        assert client.telegram_id == "555001" and client.status == "new"

        notes = session.exec(select(m.Notification).where(m.Notification.type == "LEAD")).all()
        assert len(notes) == 1 and notes[0].conversation_id is None

        events = session.exec(select(m.Event).where(m.Event.type == "lead_received")).all()
        assert len(events) == 1
        assert "+79991234567" not in str(events[0].payload)  # ПДн в журнал не попадают


def test_second_lead_from_same_person_updates_card_instead_of_duplicating(db):
    first = apply_lead(build_lead(**QUESTION))
    second = apply_lead(build_lead(**{**QUESTION, "company": "ООО Ромашка-Юг", "request": "нужен сервис"}))

    assert second["created"] is False
    assert second["client_id"] == first["client_id"]
    assert second["changed"] == ["company"]

    with session_scope() as session:
        assert len(session.exec(select(m.Client)).all()) == 1
        assert session.get(m.Client, first["client_id"]).company == "ООО Ромашка-Юг"


def test_existing_fields_are_not_overwritten_by_empty_answers(db):
    first = apply_lead(build_lead(**QUESTION))
    apply_lead(build_lead(name="Дмитрий Соколов", telegram_id="555001", request="ещё вопрос"))

    with session_scope() as session:
        client = session.get(m.Client, first["client_id"])
        assert client.company == "ООО Ромашка" and client.email == "zakaz@romashka.ru"


def test_client_is_found_by_phone_when_telegram_id_changed(db):
    """Человек написал с другого аккаунта — узнаём по телефону, карточка та же."""
    first = apply_lead(build_lead(**QUESTION))
    second = apply_lead(build_lead(**{**QUESTION, "telegram_id": "999777"}))

    assert second["client_id"] == first["client_id"] and second["created"] is False


def test_other_telegram_account_does_not_break_binding(db):
    """Привязка Telegram — это доступ клиента к своим диалогам: молча её не меняем."""
    first = apply_lead(build_lead(**QUESTION))
    second = apply_lead(build_lead(**{**QUESTION, "telegram_id": "777888", "company": None}))

    assert second["client_id"] == first["client_id"]
    assert "telegram_id" not in second["changed"]
    assert "другого аккаунта" in second["notification"]

    with session_scope() as session:
        assert session.get(m.Client, first["client_id"]).telegram_id == "555001"
        event = session.exec(select(m.Event).where(m.Event.type == "lead_received").order_by(m.Event.id.desc())).first()
        assert event.payload["alt_telegram"] is True and "777888" not in str(event.payload)


def test_empty_card_is_filled_even_if_client_exists(db):
    """Карточка без контактов (завели вручную) дозаполняется данными из анкеты."""
    with session_scope() as session:
        client = m.Client(name="Дмитрий Соколов", status="new", telegram_id="555001")
        session.add(client)
        session.flush()
        client_id = client.id

    report = apply_lead(build_lead(**QUESTION))

    assert report["client_id"] == client_id and report["created"] is False
    assert set(report["changed"]) >= {"company", "phone", "email"}
    with session_scope() as session:
        card = session.get(m.Client, client_id)
        assert card.phone == "+79991234567" and card.company == "ООО Ромашка"


def test_client_card_visible_to_site_notifications_feed(db):
    """То, ради чего всё делается: заявка сразу попадает в работу менеджера."""
    report = apply_lead(build_lead(**QUESTION))
    with session_scope() as session:
        unread = session.exec(select(m.Notification).where(m.Notification.type == "LEAD")).all()
        assert unread and unread[0].read is False
        assert f"#{report['client_id']}" in unread[0].text


def test_lead_dataclass_defaults():
    lead = Lead(name="Тест")
    assert lead.source == "telegram" and lead.phone is None


# --- Ручки /api/leads -------------------------------------------------------------------------

def test_post_lead_creates_card(client):
    resp = client.post("/api/leads", json=QUESTION, headers=TOKEN)

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["created"] is True and body["client_id"]
    assert body["name"] == "Дмитрий Соколов"


def test_post_lead_is_idempotent_for_known_phone(client):
    first = client.post("/api/leads", json=QUESTION, headers=TOKEN).json()
    second = client.post("/api/leads", json={**QUESTION, "telegram_id": "777"}, headers=TOKEN).json()

    assert second["created"] is False and second["client_id"] == first["client_id"]


def test_post_lead_validation_error_is_readable(client):
    resp = client.post("/api/leads", json={"name": "Я"}, headers=TOKEN)

    assert resp.status_code == 422
    assert "Имя" in resp.json()["detail"]  # текст можно показать клиенту в боте


def test_post_lead_requires_token(client):
    assert client.post("/api/leads", json=QUESTION).status_code == 401
    assert client.post("/api/leads", json=QUESTION, headers={"X-Bot-Token": "wrong"}).status_code == 401


def test_lead_endpoints_disabled_without_token(client, monkeypatch):
    monkeypatch.delenv("BOT_API_TOKEN")
    assert client.post("/api/leads", json=QUESTION, headers=TOKEN).status_code == 503
    assert client.get("/api/leads", headers=TOKEN).status_code == 503


def test_get_leads_returns_recent_requests_without_pii(client):
    client.post("/api/leads", json=QUESTION, headers=TOKEN)
    resp = client.get("/api/leads", params={"limit": 5}, headers=TOKEN)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["leads"][0]
    assert item["company"] == "ООО Ромашка" and item["created"] is True
    assert "+79991234567" not in str(item)  # список не разносит контакты


def test_get_leads_respects_limit(client):
    for i in range(3):
        client.post("/api/leads", json={**QUESTION, "phone": f"+7999000000{i}", "telegram_id": f"10{i}"}, headers=TOKEN)
    assert client.get("/api/leads", params={"limit": 2}, headers=TOKEN).json()["total"] == 2
