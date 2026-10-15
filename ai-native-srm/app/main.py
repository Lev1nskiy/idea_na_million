"""FastAPI-приложение: lifespan, CORS, роутеры (Шаг 5).

На старте: создание таблиц, seed политики, сброс кэшей RAG/справочников, лог доступного
LLM-провайдера и запуск фонового цикла HOLD (напоминания менеджеру и пинги клиенту).
На остановке: цикл гасится, соединения с БД закрываются.

Запуск: uvicorn app.main:app --reload --port 8000  (docs: http://localhost:8000/docs)
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.frontend import mount_spa
from app.routers import (
    admin,
    auth_api,
    clients,
    conversations,
    deals,
    messages,
    metrics,
    notifications,
    policy,
    ui_bridge,
)
from core import rag
from core.config import get_settings
from core.llm import get_llm_provider
from core.policy import seed_policy_if_empty
from core.privacy import install_pii_log_filter
from core.smooth_hold import hold_loop

logging.basicConfig(level=get_settings().log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
install_pii_log_filter()  # ПДн не попадают в открытые логи (Ст. 4)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Готовит инфраструктуру до первого запроса и корректно гасит фон при остановке."""
    db.init_db()
    if seed_policy_if_empty():
        logger.info("Policy Matrix: создана версия 1 из data/policy_seed.json")
    rag.reload()

    provider = get_llm_provider()
    logger.info("LLM-провайдер: %s", provider.name)

    stop = asyncio.Event()
    hold_task = asyncio.create_task(hold_loop(stop), name="hold_loop")
    logger.info("API готово: цикл HOLD запущен")
    try:
        yield
    finally:
        stop.set()
        hold_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hold_task
        db.dispose_engine()
        logger.info("API остановлено")


app = FastAPI(
    title="AI-native CRM API",
    description="Диалоговый ИИ-ассистент CRM: автономные решения A–H, Smooth & Hold, Policy Management.",
    version="0.5",
    lifespan=lifespan,
)

# Демо-стенд: UI (Streamlit) и каналы (Telegram) ходят с других origin — открываем CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in (
    messages.router,
    conversations.router,
    clients.router,
    deals.router,
    policy.router,
    notifications.router,
    metrics.router,
    admin.router,
    ui_bridge.public_router,  # health сайта — до входа в систему
    ui_bridge.router,  # остальная поверхность сайта (/api/site/*) — под сессией сотрудника
    auth_api.public_router,  # POST /api/site/auth/login
    auth_api.router,  # выход и «кто я»
    auth_api.users_router,  # управление сотрудниками (только администратор)
):
    app.include_router(router)


@app.get("/api/health", tags=["service"])
def health() -> dict[str, str]:
    """Проверка живости для UI, Telegram-бота и деплоя."""
    return {"status": "ok", "provider": get_llm_provider().name, "policy_version": str(_policy_version())}


def _policy_version() -> int:
    from core.policy import get_policy_version

    return get_policy_version()


# ВАЖНО: SPA подключается ПОСЛЕДНИМ. Его catch-all маршрут `/{path}` матчит что угодно,
# и если зарегистрировать его раньше, он перехватит /api/* и /docs (проверено на живом
# стенде: /api/health отвечал 404). Отдача включается только при заданном FRONTEND_DIR.
if get_settings().frontend_dir and mount_spa(app, get_settings().frontend_dir):
    logger.info("Фронтенд отдаётся из %s (SPA на том же origin)", get_settings().frontend_dir)
