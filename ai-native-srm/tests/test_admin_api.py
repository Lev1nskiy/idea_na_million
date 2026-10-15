"""Админ-API переноса данных: токен, обезличенный экспорт, dry-run импорт, справочники.

Тесты пропускаются, если нет `httpx` (TestClient) или `python-multipart` (UploadFile) —
это зависимости шага 5, они перечислены в requirements.txt.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import admin  # noqa: E402
from core.config import DATA_DIR  # noqa: E402

TOKEN = {"X-Admin-Token": "test-token"}


@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-token")
    app = FastAPI()
    app.include_router(admin.router)
    return TestClient(app)


def test_requires_admin_token(client):
    assert client.get("/api/admin/export").status_code == 401
    assert client.get("/api/admin/export", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_disabled_when_token_not_configured(client, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN")
    assert client.get("/api/admin/export", headers=TOKEN).status_code == 503


def test_export_masked_by_default(client):
    resp = client.get("/api/admin/export", headers=TOKEN)
    assert resp.status_code == 200
    assert resp.json()["masked"] is True  # наружу по умолчанию без ПДн (Ст. 4)


def test_import_dry_run_returns_report(client):
    dump = client.get("/api/admin/export", params={"mask": "false"}, headers=TOKEN).json()
    resp = client.post(
        "/api/admin/import",
        params={"dry_run": "true"},
        files={"file": ("state.json", json.dumps(dump), "application/json")},
        headers=TOKEN,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dry_run"] is True and body["committed"] is False


def test_import_rejects_masked_dump(client):
    masked = client.get("/api/admin/export", headers=TOKEN).json()
    resp = client.post(
        "/api/admin/import",
        files={"file": ("masked.json", json.dumps(masked), "application/json")},
        headers=TOKEN,
    )
    assert resp.status_code == 409  # маскированный дамп восстановить нельзя


def test_reference_data_rejects_bad_catalog(client):
    resp = client.put(
        "/api/admin/reference-data/catalog",
        files={"file": ("bad.json", b'{"wrong": []}', "application/json")},
        headers=TOKEN,
    )
    assert resp.status_code == 400


def test_reference_data_replaces_catalog(client):
    """Замена справочника атомарна и сбрасывает кэш tools (файл пишем тот же — контент идентичен)."""
    catalog = (DATA_DIR / "catalog.json").read_bytes()
    resp = client.put(
        "/api/admin/reference-data/catalog",
        files={"file": ("catalog.json", catalog, "application/json")},
        headers=TOKEN,
    )
    expected = len(json.loads(catalog)["items"])
    assert resp.status_code == 200 and resp.json()["records"] == expected
    assert (DATA_DIR / "catalog.json").read_bytes() == catalog


def test_backup_endpoint_returns_sqlite_snapshot(client, tmp_path, monkeypatch):
    """GET /api/admin/backup отдаёт бинарный снимок живой БД (файл, который восстанавливается скриптом)."""
    monkeypatch.setenv("DB_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    resp = client.get("/api/admin/backup", headers=TOKEN)
    assert resp.status_code == 200
    assert resp.content.startswith(b"SQLite format 3")
