"""Конфигурация приложения: чтение .env и переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    """Настройки, читаются из окружения при каждом вызове get_settings()."""

    db_url: str
    llm_provider: str
    gigachat_credentials: str
    gigachat_scope: str
    gigachat_model: str
    gigachat_verify_ssl: bool
    yandex_api_key: str
    yandex_folder_id: str
    admin_token: str
    tg_bot_token: str
    tg_manager_chat_id: str
    log_level: str


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    """Собирает настройки из окружения (без кэша — удобно для тестов)."""
    default_db = f"sqlite:///{(BASE_DIR / 'crm.db').as_posix()}"
    return Settings(
        db_url=os.getenv("DB_URL") or default_db,
        llm_provider=os.getenv("LLM_PROVIDER", "mock").lower(),
        gigachat_credentials=os.getenv("GIGACHAT_CREDENTIALS", ""),
        gigachat_scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        gigachat_model=os.getenv("GIGACHAT_MODEL", "GigaChat"),
        gigachat_verify_ssl=_env_bool("GIGACHAT_VERIFY_SSL", False),
        yandex_api_key=os.getenv("YANDEX_API_KEY", ""),
        yandex_folder_id=os.getenv("YANDEX_FOLDER_ID", ""),
        admin_token=os.getenv("ADMIN_TOKEN", ""),
        tg_bot_token=os.getenv("TG_BOT_TOKEN", ""),
        tg_manager_chat_id=os.getenv("TG_MANAGER_CHAT_ID", ""),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
