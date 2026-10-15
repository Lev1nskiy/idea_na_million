"""Telegram-адаптер (Шаг 6): клиентский бот + уведомления менеджеру.

Правило шага: здесь НЕТ бизнес-логики. Модуль умеет только:
1) принять текст из чата и переслать его в `POST /api/messages`;
2) забрать ответы ИИ из `GET /api/outbox?channel=telegram` и доставить в чат;
3) забрать уведомления менеджера (`DISPUTE / SECURITY / OVERRIDE_DUE`) и отправить
   их в `TG_MANAGER_CHAT_ID`, помечая прочитанными.

Вся логика адаптера вынесена в три чистые async-функции (process_client_text,
deliver_outbox, collect_manager_notifications), которые тестируются без aiogram
подменой ApiClient. aiogram импортируется лениво — модуль читается и тестируется
даже без установленного пакета.

Запуск:
    python -m channels.telegram_bot --check     # проверка настроек и доступности API
    python -m channels.telegram_bot            # long polling, клиентский бот + менеджерский поллер

Переменные окружения: TG_BOT_TOKEN, TG_MANAGER_CHAT_ID, API_URL, ADMIN_TOKEN (для
резолва telegram_id диалогов), TG_DEMO_CLIENT_ID (демо-режим для непривязанных чатов).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0  # период опроса outbox и уведомлений
MANAGER_POLL_INTERVAL_S = 5.0
CHANNEL = "telegram"

UNKNOWN_CLIENT_HINT = (
    "Здравствуйте! Этот Telegram не привязан к клиенту. Попросите менеджера связать "
    "ваш аккаунт — и я сразу смогу отвечать на вопросы о заказах, ценах и гарантии."
)
EMPTY_ANSWER_HINT = "Принял ваше сообщение, готовлю ответ — вернусь в ближайшее время."

TYPE_TITLES = {
    "DISPUTE": "⚖️ СПОР — нужен менеджер",
    "SECURITY": "🛡️ БЕЗОПАСНОСТЬ — попытка манипуляции",
    "OVERRIDE_DUE": "⏰ ТРЕБУЕТСЯ РЕШЕНИЕ — ИИ не уверен",
    "REMINDER": "🔔 НАПОМИНАНИЕ",
}


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TelegramConfig:
    """Настройки адаптера: читаются из окружения (без кэша — удобно для тестов)."""

    token: str
    manager_chat_id: str
    api_url: str
    admin_token: str
    demo_client_id: int | None
    ui_url: str = ""

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        raw_demo = os.getenv("TG_DEMO_CLIENT_ID", "").strip()
        return cls(
            token=os.getenv("TG_BOT_TOKEN", "").strip(),
            manager_chat_id=os.getenv("TG_MANAGER_CHAT_ID", "").strip(),
            api_url=os.getenv("API_URL", "http://localhost:8000").rstrip("/"),
            admin_token=os.getenv("ADMIN_TOKEN", "").strip(),
            demo_client_id=int(raw_demo) if raw_demo.isdigit() else None,
            ui_url=os.getenv("UI_URL", "").rstrip("/"),
        )


def manager_notification_text(note: dict[str, Any], ui_url: str = "") -> str:
    """Текст уведомления менеджеру: тип, клиент, сколько ждёт, напоминание, ссылка на дашборд.

    Функция чистая (никаких обращений к окружению) — так её можно проверять тестом.
    """
    head = [TYPE_TITLES.get(note.get("type", ""), "🔔 Уведомление")]
    if client := note.get("client_name"):
        head.append(str(client))
    waited = note.get("waited_min")
    if waited is not None:
        head.append(f"ждёт {waited} мин")
    if level := note.get("reminder_level"):
        head.append(f"напоминание {level}")
    body = str(note.get("text", ""))
    lines = [" · ".join(head), body]
    if "просроч" in body.lower():
        lines.append("⚠️ Срок SLA просрочен")
    if ui_url:
        lines.append(f"→ {ui_url} (диалог #{note.get('conversation_id')})")
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# HTTP-клиент к своему же API
# ---------------------------------------------------------------------------
class AsyncApi(Protocol):
    """Интерфейс, который использует логика адаптера (в тестах подменяется фейком)."""

    async def health(self) -> dict[str, Any]: ...
    async def client_by_telegram(self, telegram_id: str) -> int | None: ...
    async def send_message(self, client_id: int, text: str, conversation_id: int | None = None) -> dict[str, Any]: ...
    async def pull_outbox(self, channel: str = CHANNEL) -> list[dict[str, Any]]: ...
    async def notifications(self, unread_only: bool = True) -> list[dict[str, Any]]: ...
    async def mark_notification_read(self, notification_id: int) -> None: ...
    async def conversation_client(self, conversation_id: int) -> int | None: ...
    async def client_telegram_id(self, client_id: int) -> str | None: ...


class ApiClient:
    """Тонкая обёртка над REST API (httpx.AsyncClient). Исключения не глотает."""

    def __init__(self, base_url: str, admin_token: str = "", timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.timeout = timeout

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        import httpx  # локальный импорт: модуль остаётся импортируемым без httpx

        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
            resp = await client.request(method, path, **kwargs)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json() if resp.content else None

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/health") or {}

    async def client_by_telegram(self, telegram_id: str) -> int | None:
        data = await self._request("GET", f"/api/clients/by-telegram/{telegram_id}")
        return data["client_id"] if data else None

    async def send_message(self, client_id: int, text: str, conversation_id: int | None = None) -> dict[str, Any]:
        return await self._request(
            "POST", "/api/messages",
            json={"client_id": client_id, "conversation_id": conversation_id, "text": text, "channel": CHANNEL},
        ) or {}

    async def pull_outbox(self, channel: str = CHANNEL) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/outbox", params={"channel": channel}) or []

    async def notifications(self, unread_only: bool = True) -> list[dict[str, Any]]:
        return await self._request(
            "GET", "/api/notifications", params={"unread_only": unread_only, "active_only": True},
        ) or []

    async def mark_notification_read(self, notification_id: int) -> None:
        await self._request("POST", f"/api/notifications/{notification_id}/read")

    async def conversation_client(self, conversation_id: int) -> int | None:
        data = await self._request("GET", f"/api/conversations/{conversation_id}")
        return data["conversation"]["client_id"] if data else None

    async def client_telegram_id(self, client_id: int) -> str | None:
        """telegram_id доступен только с админ-токеном (Ст. 4)."""
        if not self.admin_token:
            return None
        data = await self._request(
            "GET", f"/api/clients/{client_id}", headers={"X-Admin-Token": self.admin_token}
        )
        return (data or {}).get("telegram_id")

    async def bound_clients(self) -> list[int]:
        """Клиенты с привязкой Telegram — для диагностики `--check`. Без админ-токена пусто."""
        if not self.admin_token:
            return []
        clients = await self._request("GET", "/api/clients") or []
        bound: list[int] = []
        for row in clients:
            if await self.client_telegram_id(row["id"]):
                bound.append(row["id"])
        return bound


# ---------------------------------------------------------------------------
# Логика адаптера (тестируется без aiogram)
# ---------------------------------------------------------------------------
async def process_client_text(
    api: AsyncApi, telegram_id: str, text: str, demo_client_id: int | None = None,
    conversation_id: int | None = None,
) -> list[str]:
    """Текст из чата → сообщения ИИ, которые надо отправить в ответ.

    Непривязанный Telegram: если задан TG_DEMO_CLIENT_ID — отвечаем от имени демо-клиента,
    иначе вежливо объясняем, что нужна привязка.
    """
    client_id = await api.client_by_telegram(telegram_id)
    if client_id is None:
        if demo_client_id is None:
            return [UNKNOWN_CLIENT_HINT]
        logger.info("Telegram %s не привязан: работаем как демо-клиент #%s", telegram_id, demo_client_id)
        client_id = demo_client_id
    response = await api.send_message(client_id, text, conversation_id=conversation_id)
    texts = [msg["text"] for msg in response.get("messages", []) if msg.get("text")]
    return texts or [EMPTY_ANSWER_HINT]


async def deliver_outbox(api: AsyncApi, channel: str = CHANNEL) -> list[tuple[str, str]]:
    """Забирает недоставленные ответы и возвращает [(chat_id, текст)] для отправки.

    chat_id определяется по привязке клиента: диалог → client_id → telegram_id.
    Результат кэшируется внутри вызова, чтобы не дёргать API на каждое сообщение.
    """
    items = await api.pull_outbox(channel)
    pairs: list[tuple[str, str]] = []
    chats: dict[int, str | None] = {}
    for item in items:
        conversation_id = item["conversation_id"]
        if conversation_id not in chats:
            client_id = await api.conversation_client(conversation_id)
            chats[conversation_id] = await api.client_telegram_id(client_id) if client_id else None
        chat_id = chats[conversation_id]
        if chat_id:
            pairs.append((chat_id, item["text"]))
        else:
            logger.warning(
                "Некуда доставить сообщение диалога #%s: нет привязки Telegram "
                "или не задан ADMIN_TOKEN (тогда telegram_id не читается)", conversation_id,
            )
    return pairs


async def collect_manager_notifications(api: AsyncApi, ui_url: str = "") -> list[str]:
    """Непрочитанные уведомления → тексты для менеджерского чата (и отметка «прочитано»).

    Повторы до takeover приходят снова: hold_loop помечает строку непрочитанной, когда
    поднимает reminder_level (п. 0 №17), поэтому менеджер видит эскалацию.
    """
    texts: list[str] = []
    for note in await api.notifications(unread_only=True):
        texts.append(manager_notification_text(note, ui_url))
        await api.mark_notification_read(note["id"])
    return texts


# ---------------------------------------------------------------------------
# aiogram: тонкая обвязка (ленивые импорты)
# ---------------------------------------------------------------------------
def build_dispatcher(bot: Any, api: AsyncApi, config: TelegramConfig) -> Any:
    """Собирает Dispatcher с обработчиками: /start, /help и любой текст клиента."""
    from aiogram import Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import Message

    dp = Dispatcher()

    @dp.message(Command("start", "help"))
    async def cmd_start(message: Message) -> None:
        client_id = await api.client_by_telegram(str(message.from_user.id)) if message.from_user else None
        if client_id:
            await message.answer(
                f"Здравствуйте! Вы в чате с ИИ-ассистентом компании (клиент #{client_id}). "
                "Спросите про товары, цены, доставку или гарантию."
            )
        else:
            await message.answer(UNKNOWN_CLIENT_HINT)

    @dp.message(F.text)
    async def on_text(message: Message) -> None:
        if message.from_user is None or not message.text:
            return
        for text in await process_client_text(api, str(message.from_user.id), message.text, config.demo_client_id):
            await message.answer(text)

    return dp


async def deliver_outbox_loop(bot: Any, api: AsyncApi, interval: float = POLL_INTERVAL_S) -> None:
    """Бесконечный цикл: доставка ответов ИИ клиентам."""
    while True:
        try:
            for chat_id, text in await deliver_outbox(api):
                await bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:  # цикл не должен падать из-за разовой ошибки сети
            logger.error("outbox loop: %s", type(exc).__name__)
        await asyncio.sleep(interval)


async def notify_manager_loop(
    bot: Any, api: AsyncApi, manager_chat_id: str, ui_url: str = "", interval: float = MANAGER_POLL_INTERVAL_S
) -> None:
    """Бесконечный цикл: уведомления менеджеру (DISPUTE / SECURITY / OVERRIDE_DUE)."""
    while True:
        try:
            for text in await collect_manager_notifications(api, ui_url):
                await bot.send_message(chat_id=manager_chat_id, text=text)
        except Exception as exc:
            logger.error("manager loop: %s", type(exc).__name__)
        await asyncio.sleep(interval)


async def run() -> int:
    """Запуск long polling: клиентский бот + поллер outbox + поллер уведомлений."""
    from aiogram import Bot

    config = TelegramConfig.from_env()
    if not config.token:
        logger.error("TG_BOT_TOKEN не задан — бот не запущен. Смотри инструкцию в README (шаг 6).")
        return 1

    api = ApiClient(config.api_url, config.admin_token)
    bot = Bot(token=config.token)
    tasks = [asyncio.create_task(deliver_outbox_loop(bot, api))]
    if config.manager_chat_id:
        tasks.append(asyncio.create_task(notify_manager_loop(bot, api, config.manager_chat_id, config.ui_url)))
    else:
        logger.warning("TG_MANAGER_CHAT_ID не задан: уведомления менеджеру отправляться не будут")

    logger.info("Telegram-бот запущен (API: %s, менеджерский чат: %s)", config.api_url, config.manager_chat_id or "—")
    try:
        await build_dispatcher(bot, api, config).start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()
    return 0


async def check() -> int:
    """Диагностика перед защитой: настройки, доступность API, режим демо-клиента."""
    config = TelegramConfig.from_env()
    print("Проверка Telegram-адаптера")
    print(f"  TG_BOT_TOKEN:       {'задан' if config.token else 'НЕ ЗАДАН (бот не запустится)'}")
    print(f"  TG_MANAGER_CHAT_ID: {config.manager_chat_id or 'НЕ ЗАДАН (уведомления менеджеру отключены)'}")
    print(f"  API_URL:            {config.api_url}")
    print(f"  ADMIN_TOKEN:        {'задан (резолв telegram_id работает)' if config.admin_token else 'не задан (доставка ответов без привязки невозможна)'}")
    print(f"  TG_DEMO_CLIENT_ID:  {config.demo_client_id if config.demo_client_id else '— (непривязанные чаты получат подсказку)'}")
    api = ApiClient(config.api_url, config.admin_token)
    try:
        info = await api.health()
        print(f"  API:                онлайн, провайдер {info.get('provider')}, политика v{info.get('policy_version')}")
    except Exception as exc:
        print(f"  API:                НЕДОСТУПЕН ({type(exc).__name__}) — запустите uvicorn app.main:app")
        return 1
    try:
        if not config.admin_token:
            print("  Привязки Telegram:  неизвестны (нужен ADMIN_TOKEN)")
        else:
            bound = await api.bound_clients()
            where = ", ".join(f"#{i}" for i in bound) if bound else "нет ни одного клиента"
            print(f"  Привязки Telegram:  {where}")
            print("                      привязать: python scripts/bind_telegram.py --client <id> --telegram-id <id>")
    except Exception as exc:  # диагностика не должна падать из-за одной недоступной ручки
        print(f"  Привязки Telegram:  не удалось получить ({type(exc).__name__})")
    return 0


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Telegram-адаптер AI-native CRM")
    parser.add_argument("--check", action="store_true", help="проверить настройки и API, не подключаясь к Telegram")
    args = parser.parse_args()
    code = asyncio.run(check() if args.check else run())
    sys.exit(code)


if __name__ == "__main__":
    main()
