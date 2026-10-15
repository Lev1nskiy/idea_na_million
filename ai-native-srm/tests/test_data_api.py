"""Кнопки переноса данных на сайте: общий модуль app/transfer.py и ручки /api/site/data/*.

Проверяем то, ради чего модуль вынесен: админ-API и ручки сайта ходят по одним правилам,
изменение видно и там, и там, а доступ к ПДн остаётся только у администратора.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from app import transfer  # noqa: E402
from app.main import app  # noqa: E402

QUESTIONS = [
    {"name": "Дмитрий Соколов", "company": "ООО Ромашка", "phone": "+79991234567", "email": "d@sokolov.ru", "request": "10 принтеров"},
    {"name": "Анна Петрова", "company": "АО Вектор", "phone": "+79005554433", "email": "a@vektor.ru", "request": "сервис парка"},
]


def login(client: TestClient, login_value: str, password: str) -> str:
    resp = client.post("/api/site/auth/login", json={"login": login_value, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest.fixture()
def api(db, monkeypatch):
    """Администратор — он и только он видит страницу «Данные»."""
    monkeypatch.setenv("BOT_API_TOKEN", "bot-secret")  # заявки бота: ими и наполняем базу
    with TestClient(app) as client:
        client.headers.update({"X-Auth-Token": login(client, "admin", "admin")})
        yield client


@pytest.fixture()
def manager_api(db):
    with TestClient(app) as client:
        client.headers.update({"X-Auth-Token": login(client, "manager", "manager")})
        yield client


def _fill(api: TestClient) -> None:
    """Данные для дампа: две заявки через бота — так в базе появляются живые клиенты."""
    for question in QUESTIONS:
        resp = api.post("/api/leads", json=question, headers={"X-Bot-Token": "bot-secret"})
        assert resp.status_code == 200, resp.text


# --- Доступ -----------------------------------------------------------------------------------

def test_data_page_requires_login(db):
    with TestClient(app) as client:
        assert client.get("/api/site/data/summary").status_code == 401
        assert client.get("/api/site/data/export").status_code == 401


def test_manager_cannot_touch_data_endpoints(manager_api):
    """Дамп — это персональные данные и вся база: менеджеру отказ, а не маскированная копия."""
    for path in ("summary", "export", "backup"):
        assert manager_api.get(f"/api/site/data/{path}").status_code == 403


def test_admin_sees_summary(api):
    body = api.get("/api/site/data/summary").json()

    assert "clients" in body["tables"] and "messages" in body["tables"]
    assert body["db_size_bytes"] > 0 and body["db_file"].endswith((".db", ".sqlite3"))
    assert body["policy_version"] >= 0


# --- Экспорт ----------------------------------------------------------------------------------

def test_export_is_masked_by_default_and_has_filename(api):
    resp = api.get("/api/site/data/export")

    assert resp.status_code == 200
    assert "crm-state-" in resp.headers["content-disposition"]
    assert resp.json()["masked"] is True


def test_full_export_contains_client_card(api):
    _fill(api)
    body = api.get("/api/site/data/export", params={"mask": "false"}).json()

    assert body["schema_version"] == 1 and body["masked"] is False
    assert any(client.get("phone") == "+79991234567" for client in body["tables"]["clients"])


def test_backup_returns_sqlite_file(api):
    resp = api.get("/api/site/data/backup")

    assert resp.status_code == 200
    assert resp.content[:16].startswith(b"SQLite format 3")  # настоящий файл, а не JSON
    assert "attachment" in resp.headers["content-disposition"]


# --- Импорт -----------------------------------------------------------------------------------

def test_import_dry_run_changes_nothing_and_reports(api):
    dump = api.get("/api/site/data/export", params={"mask": "false"}).json()
    before = api.get("/api/site/data/summary").json()["tables"]

    resp = api.post(
        "/api/site/data/import",
        params={"dry_run": "true"},
        files={"file": ("state.json", json.dumps(dump), "application/json")},
    )

    assert resp.status_code == 200
    assert resp.json()["dry_run"] is True and resp.json()["committed"] is False
    assert api.get("/api/site/data/summary").json()["tables"] == before


def test_import_merge_puts_data_back_into_empty_db(api):
    """Перенос между контурами: дамп → чистая база (правило «сколько было — столько и стало»)."""
    _fill(api)
    dump = api.get("/api/site/data/export", params={"mask": "false"}).json()
    clients_before = api.get("/api/site/data/summary").json()["tables"]["clients"]

    resp = api.post(
        "/api/site/data/import",
        params={"mode": "merge"},
        files={"file": ("state.json", json.dumps(dump), "application/json")},
    )

    assert resp.status_code == 200
    assert resp.json()["committed"] is True
    assert api.get("/api/site/data/summary").json()["tables"]["clients"] == clients_before  # без дублей


def test_masked_dump_is_refused_with_readable_error(api):
    masked = api.get("/api/site/data/export").json()
    resp = api.post(
        "/api/site/data/import",
        files={"file": ("masked.json", json.dumps(masked), "application/json")},
    )

    assert resp.status_code == 409
    assert "маск" in resp.json()["detail"].lower() or "обезличен" in resp.json()["detail"].lower()


def test_broken_file_is_refused(api):
    resp = api.post("/api/site/data/import", files={"file": ("state.json", "{не json", "application/json")})

    assert resp.status_code == 400 and "JSON" in resp.json()["detail"]


# --- Общность правил ----------------------------------------------------------------------------

def test_same_rules_as_admin_api(api, monkeypatch):
    """Логика одна: admin-API и ручки сайта дают одинаковые дампы по существу."""
    monkeypatch.setenv("ADMIN_TOKEN", "admin-token")
    _fill(api)

    via_site = api.get("/api/site/data/export", params={"mask": "false"}).json()
    via_admin = api.get("/api/admin/export", params={"mask": "false"}, headers={"X-Admin-Token": "admin-token"}).json()

    assert via_site["schema_version"] == via_admin["schema_version"]
    assert set(via_site["tables"]) == set(via_admin["tables"])
    assert via_site["counts"] == via_admin["counts"]


def test_import_through_admin_api_still_works_after_refactor(api, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "admin-token")
    dump = api.get("/api/site/data/export", params={"mask": "false"}).json()

    resp = api.post(
        "/api/admin/import",
        params={"dry_run": "true"},
        files={"file": ("state.json", json.dumps(dump), "application/json")},
        headers={"X-Admin-Token": "admin-token"},
    )

    assert resp.status_code == 200 and resp.json()["dry_run"] is True


def test_backup_name_is_dated(db):
    """Имя снимка содержит дату-время: снимки не перезаписывают друг друга."""
    import re

    name = transfer.build_backup().headers["content-disposition"]
    assert re.search(r"crm-\d{4}-\d{2}-\d{2}-\d{4}\.sqlite3", name)
