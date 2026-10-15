"""Отдача собранного фронтенда (SPA) с того же origin, что и API.

Зачем это нужно: если сайт (React/Vue/Next в режиме статики) собран в каталог с `index.html`,
его можно отдать тем же FastAPI. Тогда:

* один HTTPS-URL для жюри (и для коллег), а не два;
* нет CORS-проблем и «смешанного контента»;
* фронт и API всегда одной версии — не разъедутся при деплое.

Включается переменной окружения `FRONTEND_DIR` (см. `.env.example`). Если она не задана или
каталога нет — приложение работает как чистое API, как раньше (поведение по умолчанию).

Маршрутизация: существующий файл отдаётся как есть, неизвестный путь — `index.html` (это нужно
для клиентских роутов вида `/dashboard`: браузер запросит их у сервера, а 404 там недопустим).
Пути `/api/*`, `/docs`, `/redoc`, `/openapi.json` не перехватываются никогда.

ВАЖНО про порядок: функция добавляет catch-all маршрут `/{path}`, поэтому вызывать её нужно
ПОСЛЕ регистрации всех API-роутов — иначе она их перехватит (Starlette выбирает первый
подошедший маршрут). В `app/main.py` вызов стоит последним.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

# Префиксы, которые принадлежат API и Swagger: SPA не имеет права их перехватывать.
RESERVED_PREFIXES = ("api/", "api", "docs", "redoc", "openapi.json")


def mount_spa(app: FastAPI, directory: str | Path) -> bool:
    """Подключает раздачу статики к приложению. True — если каталог найден и отдача включена."""
    root = Path(directory).resolve()
    if not root.is_dir():
        return False
    index = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str) -> FileResponse:
        if full_path.startswith(RESERVED_PREFIXES):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (root / full_path).resolve()
        # Защита от выхода за каталог сборки (../): разрешаем только пути внутри root.
        if candidate != root and root not in candidate.parents:
            raise HTTPException(status_code=404, detail="Not found")
        if candidate.is_file():
            return FileResponse(candidate)
        if index.is_file():
            return FileResponse(index)  # SPA-роут: отдаём приложение, дальше маршрутизирует оно
        raise HTTPException(status_code=404, detail="Сборка фронтенда не найдена: нет index.html")

    app.state.frontend_dir = str(root)
    return True
