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
    llm_timeout_s: float
    gigachat_credentials: str
    gigachat_scope: str
    gigachat_model: str
    gigachat_verify_ssl: bool
    gigachat_ca_bundle_file: str
    gigachat_max_tokens: int
    yandex_api_key: str
    yandex_iam_token: str
    yandex_folder_id: str
    yandex_model: str
    yandex_model_uri: str
    yandex_max_tokens: int
    yandex_reasoning_mode: str
    local_model: str
    local_base_url: str
    local_api_key: str
    local_max_tokens: int
    admin_token: str
    bot_api_token: str
    frontend_dir: str
    tg_bot_token: str
    tg_lead_bot_token: str
    tg_manager_chat_id: str
    log_level: str


def _env_int(name: str, default: int) -> int:
    """Целое из окружения; мусор в переменной не должен ронять сервис."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    """Дробное из окружения (таймауты задаются в секундах)."""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


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
        # auto — «облако, если ключи заданы; иначе mock». Явное имя провайдера или их список
        # через запятую (LLM_PROVIDER=yandex,gigachat) задаёт порядок цепочки fallback.
        llm_provider=os.getenv("LLM_PROVIDER", "auto").lower(),
        # Бюджет времени на один вызов модели. Облачные модели не всегда укладываются в доли
        # секунды: если ответ не успел — цепочка уходит к следующему провайдеру и дальше в mock.
        llm_timeout_s=_env_float("LLM_TIMEOUT_S", 2.5),
        gigachat_credentials=os.getenv("GIGACHAT_CREDENTIALS", ""),
        gigachat_scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        gigachat_model=os.getenv("GIGACHAT_MODEL", "GigaChat-2"),
        gigachat_verify_ssl=_env_bool("GIGACHAT_VERIFY_SSL", False),
        # Корневой сертификат НУЦ Минцифры (файл .cer/.crt из gosuslugi.ru/crt) — предпочтительнее,
        # чем выключать проверку TLS целиком.
        gigachat_ca_bundle_file=os.getenv("GIGACHAT_CA_BUNDLE_FILE", ""),
        gigachat_max_tokens=_env_int("GIGACHAT_MAX_TOKENS", 0),  # 0 — лимит модели по умолчанию
        yandex_api_key=os.getenv("YANDEX_API_KEY", ""),
        # IAM-токен (12 ч) как альтернатива API-ключу: Authorization: Bearer вместо Api-Key.
        yandex_iam_token=os.getenv("YANDEX_IAM_TOKEN", ""),
        yandex_folder_id=os.getenv("YANDEX_FOLDER_ID", ""),
        # Идентификаторы моделей: yandexgpt-5-lite | yandexgpt-5-pro | yandexgpt-5.1.
        # Модели 4-го поколения (yandexgpt, yandexgpt-lite) выведены 28.08.2025 — нужен суффикс
        # версии: YANDEX_MODEL=yandexgpt-lite/latest.
        yandex_model=os.getenv("YANDEX_MODEL", "yandexgpt-5-lite"),
        yandex_model_uri=os.getenv("YANDEX_MODEL_URI", ""),  # полный gpt://… (дообученные модели)
        yandex_max_tokens=_env_int("YANDEX_MAX_TOKENS", 800),
        # Пусто — режим рассуждений не трогаем (кроме моделей 5-го поколения: там выключаем,
        # чтобы ответ был коротким и быстрым). DISABLED/ENABLED — задать явно.
        yandex_reasoning_mode=os.getenv("YANDEX_REASONING_MODE", "").lower(),
        # Локальный OpenAI-совместимый сервер (Ollama, LM Studio, llama.cpp, vLLM).
        # Пусто → провайдер выключен; ключ нужен только сторонним шлюзам, локальным — нет.
        local_model=os.getenv("LOCAL_MODEL", ""),
        local_base_url=os.getenv("LOCAL_BASE_URL", "http://127.0.0.1:11434/v1"),
        local_api_key=os.getenv("LOCAL_API_KEY", ""),
        local_max_tokens=_env_int("LOCAL_MAX_TOKENS", 800),
        admin_token=os.getenv("ADMIN_TOKEN", ""),
        # Сервисный токен Telegram-бота-анкеты: им защищены POST/GET /api/leads.
        bot_api_token=os.getenv("BOT_API_TOKEN", ""),
        frontend_dir=os.getenv("FRONTEND_DIR", ""),
        tg_bot_token=os.getenv("TG_BOT_TOKEN", ""),
        # Отдельный бот-анкета: собирает данные и заводит карточку клиента (не чат-бот).
        tg_lead_bot_token=os.getenv("TG_LEAD_BOT_TOKEN", ""),
        tg_manager_chat_id=os.getenv("TG_MANAGER_CHAT_ID", ""),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
