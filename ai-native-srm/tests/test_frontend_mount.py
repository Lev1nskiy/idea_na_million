"""Отдача собранного фронтенда с того же origin, что и API (см. app/frontend.py).

Проверяем то, что легко сломать: API не перехватывается SPA-роутом, deep links отдают
index.html, статика отдаётся как есть, выход за каталог сборки закрыт.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend import mount_spa


def _app(tmp_path: Path) -> TestClient:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (tmp_path / "index.html").write_text("<h1>Сайт коллег</h1>", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    assert mount_spa(app, tmp_path) is True
    return TestClient(app)


def test_index_and_deep_links_serve_spa(tmp_path):
    client = _app(tmp_path)

    assert client.get("/").text == "<h1>Сайт коллег</h1>"
    # клиентская маршрутизация: /dashboard запрашивается у сервера и должен получить приложение
    assert client.get("/dashboard").text == "<h1>Сайт коллег</h1>"
    assert client.get("/deals/42").text == "<h1>Сайт коллег</h1>"


def test_static_assets_served_as_files(tmp_path):
    client = _app(tmp_path)
    resp = client.get("/assets/app.js")

    assert resp.status_code == 200 and resp.text.startswith("console.log")


def test_api_is_never_swallowed_by_spa(tmp_path):
    client = _app(tmp_path)

    assert client.get("/api/health").json() == {"status": "ok"}   # живой API-роут
    assert client.get("/api/unknown").status_code == 404          # неизвестный API-путь — 404, а не index.html
    assert client.get("/api/clients").status_code == 404          # в этом тестовом приложении его нет


def test_missing_directory_is_not_an_error(tmp_path):
    app = FastAPI()
    assert mount_spa(app, tmp_path / "нет-такого-каталога") is False  # приложение остаётся чистым API


def test_path_traversal_is_blocked(tmp_path):
    client = _app(tmp_path)
    resp = client.get("/..%2F..%2Fcore%2Fconfig.py")

    assert "DATA_DIR" not in resp.text, "содержимое вне каталога сборки отдавать нельзя"


def test_spa_registered_last_does_not_swallow_existing_routes(tmp_path):
    """Регрессия: если SPA подключить до API-роутов, catch-all уводит /api/* в 404."""
    (tmp_path / "index.html").write_text("<h1>Сайт</h1>", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/metrics")
    def metrics() -> dict[str, str]:
        return {"ok": "yes"}

    mount_spa(app, tmp_path)          # после API — правильный порядок
    client = TestClient(app)

    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/metrics").json() == {"ok": "yes"}
    assert client.get("/dashboard").text == "<h1>Сайт</h1>"
