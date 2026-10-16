"""Telegram-бот компании: меню, профиль, обращения, чат с ИИ и вызов менеджера.

Один бот вместо двух. Раньше каналов было два: бот-анкета (собирал заявку) и клиентский
чат-бот (пересылал текст в пайплайн), и клиенту приходилось угадывать, какой из них за что
отвечает. Теперь это один бот с меню, а разделы живут рядом: профиль, обращения, чат с ИИ,
вызов менеджера, база знаний и каталог.

Модуль — тонкий адаптер (правило Шага 6: в канале нет бизнес-логики):
    • экраны и кнопки — `channels/bot_ui.py`;
    • логика диалога и состояния — `channels/bot_flow.py`;
    • CRM — фасад `/api/bot/*` (`app/routers/bot_api.py`) через `BotApi`;
    • офлайн-прогон без токенов и сети — `python -m channels.mock_telegram`.

Запуск:
    python -m channels.telegram_lead_bot --check          # настройки, Telegram, API
    python -m channels.telegram_lead_bot                  # long polling
    python -m channels.telegram_lead_bot --mock           # весь диалог в терминале (mock-CRM)
    python -m channels.telegram_lead_bot --drop-webhook   # снять webhook, если был

Переменные окружения:
    TG_LEAD_BOT_TOKEN   — токен бота от @BotFather (обязателен для запуска)
    BOT_API_TOKEN       — сервисный токен для /api/bot/* (тот же, что в .env бэкенда)
    API_URL             — адрес API (по умолчанию http://localhost:8000)
    TG_MANAGER_CHAT_ID  — чат менеджера: туда идут уведомления и копии заявок (можно списком)
    UI_URL              — адрес дашборда для ссылки в уведомлении (необязательно)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import html
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from channels import bot_ui as ui
from channels.bot_flow import BotError, BotSession, Event, Out
from channels.manager_feed import manager_notification_text
from core import rag
from core.tools import catalog_items

logger = logging.getLogger(__name__)

API_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 2.0  # период проверки: есть ли что доставить клиенту
MANAGER_POLL_INTERVAL_S = 5.0  # период проверки уведомлений для менеджера
WIDGET_HINT = "⌨️ Разделы всегда под рукой — кнопки меню внизу экрана."
#: Команды бота: показываются в списке Telegram (кнопка «Меню» рядом с полем ввода).
BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "меню и разделы"),
    ("profile", "мой профиль"),
    ("appeals", "мои обращения"),
    ("chat", "спросить ИИ"),
    ("manager", "позвать менеджера"),
    ("id", "мой номер для менеджера"),
    ("cancel", "отменить заполнение"),
)
BOT_ABOUT = "Бот компании «ЛОСЬ»: подскажу наличие и цены, приму обращение, отвечу на вопросы и позову менеджера."
BOT_ABOUT_LONG = (
    "Я отвечаю на вопросы про товары, цены, доставку, гарантию и ремонт, а сложное передаю менеджеру.\n"
    "Переписка попадает в CRM компании — история не теряется.\n\n"
    "Разделы: «👤 Профиль», «📁 Мои обращения», «🆕 Обращение», «🤖 Чат с ИИ», "
    "«💬 Менеджер», «❓ Помощь», «🖥 Каталог», «⚙️ Настройки»."
)
#: Подсказка для случая «меню не видно»: Telegram показывает клавиатуру после команды.
START_HINT = "Клавиатуру Telegram показывает после команды — отправьте боту /start."


# --------------------------------------------------------------------------- настройки
@dataclass(frozen=True)
class LeadBotConfig:
    """Настройки бота: читаются из окружения (без кэша — удобно для тестов)."""

    token: str
    api_url: str
    api_token: str
    manager_chat_id: str = ""
    ui_url: str = ""

    @classmethod
    def from_env(cls) -> "LeadBotConfig":
        return cls(
            token=os.getenv("TG_LEAD_BOT_TOKEN", "").strip(),
            api_url=os.getenv("API_URL", "http://localhost:8000").rstrip("/"),
            api_token=os.getenv("BOT_API_TOKEN", "").strip(),
            manager_chat_id=os.getenv("TG_MANAGER_CHAT_ID", "").strip(),
            ui_url=os.getenv("UI_URL", "").rstrip("/"),
        )

    @property
    def ready(self) -> bool:
        return bool(self.token and self.api_token)


def parse_manager_chats(config: LeadBotConfig) -> list[int]:
    """TG_MANAGER_CHAT_ID может содержать несколько id через запятую."""
    chats: list[int] = []
    for raw in config.manager_chat_id.split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            chats.append(int(value))
        except ValueError:
            logger.warning("TG_MANAGER_CHAT_ID: «%s» не похоже на chat_id — пропускаю", value)
    return chats


# --------------------------------------------------------------------------- клиент CRM
class BotApi:
    """Клиент `/api/bot/*`: профиль, обращения, переписка. Реализует `BotService`.

    Бот не ходит по внутренним ручкам дашборда: у него свой фасад и свой токен. Каждый
    экземпляр привязан к одному чату (`telegram_id`), поэтому чужой диалог через бота
    открыть нельзя — сервер сверяет владельца обращения.
    """

    def __init__(self, config: LeadBotConfig, telegram_id: str = "", display_name: str = "", username: str = "") -> None:
        self._config = config
        self.telegram_id = telegram_id
        self.display_name = display_name
        self.username = username

    # ------------------------------------------------------------------ HTTP
    def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
        url = f"{self._config.api_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, '')})}"
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json", "X-Bot-Token": self._config.api_token},
        )
        try:
            with urllib.request.urlopen(request, timeout=API_TIMEOUT_S) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except json.JSONDecodeError:
                pass
            if exc.code == 401:
                raise BotError("CRM отклонила токен бота (BOT_API_TOKEN) — проверьте .env", exc.code) from exc
            if exc.code == 503:
                raise BotError("Приём сообщений в CRM выключен: не задан BOT_API_TOKEN", exc.code) from exc
            raise BotError(f"CRM ответила ошибкой: {detail}", exc.code) from exc
        except urllib.error.URLError as exc:
            raise BotError("CRM недоступна — проверьте, запущен ли API (uvicorn app.main:app)") from exc

    # ------------------------------------------------------------------ BotService
    def profile(self) -> dict[str, Any] | None:
        payload = self._call("GET", "/api/bot/me", params={"telegram_id": self.telegram_id})
        return (payload or {}).get("client") if (payload or {}).get("found") else None

    def save_profile(self, answers: dict[str, str]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "telegram_id": self.telegram_id,
            "name": answers.get("name") or self.display_name or "Клиент Telegram",
        }
        for field in ("company", "phone", "email"):
            if answers.get(field):
                body[field] = answers[field]
        if self.username:
            body["username"] = self.username
        return self._call("POST", "/api/bot/me", body=body)

    def appeals(self, open_only: bool = False) -> list[dict[str, Any]]:
        payload = self._call(
            "GET", "/api/bot/me/appeals", params={"telegram_id": self.telegram_id, "open_only": "true" if open_only else ""}
        )
        return (payload or {}).get("appeals", [])

    def appeal(self, conversation_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        payload = self._call(
            "GET", f"/api/bot/me/appeals/{conversation_id}", params={"telegram_id": self.telegram_id}
        ) or {}
        return payload.get("appeal", {}), payload.get("messages", [])

    def chat(self, text: str, conversation_id: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"telegram_id": self.telegram_id, "text": text, "conversation_id": conversation_id}
        if self.display_name:  # пустые поля не отправляем: сервер сам подставит разумные значения
            body["display_name"] = self.display_name
        if self.username:
            body["username"] = self.username
        return self._call("POST", "/api/bot/messages", body=body) or {}

    def call_manager(self, conversation_id: int, reason: str = "") -> str:
        payload = self._call(
            "POST",
            f"/api/bot/me/appeals/{conversation_id}/manager",
            params={"telegram_id": self.telegram_id, "reason": reason},
            body={"telegram_id": self.telegram_id},
        ) or {}
        return str(payload.get("text", "Менеджер уведомлён — он ответит здесь."))

    def close_appeal(self, conversation_id: int) -> str:
        payload = self._call(
            "POST",
            f"/api/bot/me/appeals/{conversation_id}/close",
            body={"telegram_id": self.telegram_id},
        ) or {}
        return str(payload.get("text", "Обращение закрыто."))

    def catalog(self) -> list[dict[str, Any]]:
        """Каталог читаем из справочника сами — те же данные, что у ИИ (без лишнего запроса)."""
        return catalog_items()

    def faq(self) -> list[tuple[str, str]]:
        """База знаний — тоже локальный файл: это общая справка без данных клиентов (Ст. 4)."""
        return [(chunk.title, chunk.text) for chunk in rag.sections()]

    # ------------------------------------------------------------------ доставка клиенту
    def inbox(self) -> list[dict[str, Any]]:
        """Забирает недоставленные ответы ИИ и менеджера для этого клиента."""
        try:
            payload = self._call("POST", "/api/bot/me/inbox", body={"telegram_id": self.telegram_id}) or {}
        except BotError as exc:
            if exc.status == 404:  # писать ещё некому: карточки в CRM нет
                return []
            raise
        return payload.get("items", [])

    def manager_reply(self, conversation_id: int, text: str, manager: str = "Ирина (менеджер)") -> None:
        """Ответ менеджера из дашборда — нужен только mock-транспорту, чтобы показать доставку.

        В реальной работе этого вызова нет: менеджер отвечает в дашборде сайта, а бот лишь
        забирает готовый ответ из очереди (`inbox`).
        """
        self._call(
            "POST",
            f"/api/conversations/{conversation_id}/manager-reply",
            body={"manager": manager, "text": text},
        )

    # ------------------------------------------------------------------ уведомления менеджеру
    def manager_notifications(self) -> list[dict[str, Any]]:
        """Непрочитанные уведомления для ленты менеджера в Telegram (Ст. 8: дубли до takeover)."""
        return self._call("GET", "/api/notifications", params={"unread_only": "true", "active_only": "true"}) or []

    def mark_notification_read(self, notification_id: int) -> None:
        self._call("POST", f"/api/notifications/{notification_id}/read")

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/api/health") or {}


# --------------------------------------------------------------------------- диагностика Telegram
def telegram_call(token: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Вызов Telegram Bot API — нужен диагностике (getMe, getWebhookInfo, deleteWebhook).

    Ошибки не бросаем, а возвращаем как {ok: false, description}: диагностика должна
    объяснять проблему, а не падать самой.
    """
    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=API_TIMEOUT_S) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "description": f"HTTP {exc.code}: {raw[:200]}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "description": f"нет связи с Telegram: {exc.reason}"}


async def setup_commands(bot: Any) -> None:
    """Публикует список команд и описание бота в Telegram (кнопка «Меню» рядом с полем ввода).

    Для клиента это часть «виджет-меню»: нажав на кнопку меню, он видит /profile, /appeals,
    /chat, /manager. Ошибки не считаем критичными: без них бот продолжает работать.
    """
    from aiogram.types import BotCommand

    with contextlib.suppress(Exception):
        await bot.set_my_commands([BotCommand(command=name, description=text) for name, text in BOT_COMMANDS])
    with contextlib.suppress(Exception):
        await bot.set_my_short_description(short_description=BOT_ABOUT)
    with contextlib.suppress(Exception):
        await bot.set_my_description(description=BOT_ABOUT_LONG)


def drop_webhook(config: LeadBotConfig) -> int:
    """Снимает webhook у бота и чистит накопившиеся апдейты.

    Зачем: если у бота когда-то был установлен webhook (например, при попытке развернуть приём
    на сервере), Telegram отдаёт апдейты по нему, а не в long polling — и запуск бота падает
    с TelegramConflictError. Эта команда возвращает бота к работе через getUpdates.
    """
    result = telegram_call(config.token, "deleteWebhook", {"drop_pending_updates": "true"})
    if result.get("ok"):
        print("Webhook снят, накопившиеся сообщения удалены — можно запускать бота: python -m channels.telegram_lead_bot")
        return 0
    print(f"Не получилось снять webhook: {result.get('description', 'неизвестная ошибка')}")
    print("Проверьте TG_LEAD_BOT_TOKEN: возможно, он неверный или отозван в @BotFather.")
    return 1


async def probe_polling_free(bot: Any) -> str | None:
    """Проверяет, свободен ли бот для long polling.

    Делает безопасный getUpdates (offset=-1 ничего не подтверждает и не удаляет) и возвращает
    причину, если бота уже опрашивает кто-то другой: второй запущенный экземпляр или webhook.
    Без этой проверки aiogram уходит в бесконечные повторы с TelegramConflictError.
    """
    try:
        from aiogram.exceptions import TelegramConflictError
    except ImportError:  # aiogram нет — проверять нечего
        return None
    try:
        await bot.get_updates(offset=-1, limit=1, timeout=0)
        return None
    except TelegramConflictError as exc:
        return str(exc)


CONFLICT_HINT = """Бот уже занят другим экземпляром — Telegram разрешает только один long polling на токен.
  • найти лишние процессы (окна/службы с тем же ботом):
      Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*telegram_lead_bot*" } |
        Select-Object ProcessId, CommandLine
      затем: Stop-Process -Id <PID> -Force      (uvicorn не затронется)
  • если бот запускался в Docker: docker ps → docker stop <container>
  • если у бота стоял webhook (пробовали приём на сервере):
      python -m channels.telegram_lead_bot --drop-webhook
  • после этого запустите бота заново — только в одном окне."""


# --------------------------------------------------------------------------- клавиатуры
def widget_keyboard() -> Any:
    """Нижнее меню-виджет: постоянные разделы, доступные одним нажатием (aiogram-типы лениво)."""
    from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in ui.widget_menu()],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите раздел или напишите вопрос",
    )


def inline_keyboard(rows: tuple[tuple[ui.Button, ...], ...]) -> Any:
    """Inline-кнопки экрана (навигация по разделам и действия с обращением)."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    buttons = [
        [
            InlineKeyboardButton(text=b.text, url=b.url) if b.url else InlineKeyboardButton(text=b.text, callback_data=b.data)
            for b in row
        ]
        for row in rows
        if row
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


def delivery_text(item: dict[str, Any]) -> str:
    """Оформление сообщения, пришедшего из CRM (ответ менеджера или догоняющий ответ ИИ)."""
    body = html.escape(str(item.get("text", "")))
    role = item.get("role", "")
    if role == "manager":
        return f"🧑‍💼 <b>Менеджер:</b>\n{body}"
    if role == "ai":
        return f"🤖 {body}"
    return body


# --------------------------------------------------------------------------- aiogram
def build_dispatcher(bot: Any, config: LeadBotConfig) -> Any:
    """Собирает Dispatcher: сессии по чатам, экраны из bot_flow, виджет и outbox-поллер."""
    from aiogram import Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, Message

    sessions: dict[str, BotSession] = {}
    api_clients: dict[str, BotApi] = {}
    widget_ready: set[str] = set()
    dispatcher = Dispatcher()

    def api_for(chat_key: str) -> BotApi:
        return api_clients[chat_key]

    def session_for(message: Message) -> BotSession:
        key = str(message.chat.id)
        if key not in sessions:
            user = message.from_user
            api = BotApi(
                config,
                telegram_id=key,
                display_name=getattr(user, "full_name", "") or getattr(user, "first_name", ""),
                username=getattr(user, "username", "") or "",
            )
            api_clients[key] = api
            sessions[key] = BotSession(api, telegram_id=key, display_name=api.display_name, username=api.username)
        return sessions[key]

    async def render(message: Message, chat_key: str, out: Out) -> None:
        """Показывает экран: inline-кнопки — правкой сообщения, иначе новым сообщением.

        Нижний виджет-меню Telegram разрешает только у нового сообщения, поэтому он
        ставится один раз за диалог, а навигация идёт inline-кнопками без спама.
        """
        inline = inline_keyboard(out.screen.rows)
        if out.edit and inline is not None:
            with contextlib.suppress(Exception):  # сообщение могло устареть — тогда отправим новое
                await message.edit_text(out.screen.text, reply_markup=inline, parse_mode="HTML")
                return
        await message.answer(
            out.screen.text,
            reply_markup=inline or (widget_keyboard() if out.screen.menu else None),
            parse_mode="HTML",
        )
        if out.screen.menu and chat_key not in widget_ready:
            widget_ready.add(chat_key)
            await message.answer(WIDGET_HINT, reply_markup=widget_keyboard())

    async def dispatch(message: Message, event: Event) -> None:
        chat_key = str(message.chat.id)
        session = session_for(message)
        for out in session.handle(event):
            await render(message, chat_key, out)

    @dispatcher.message(Command("start", "menu", "help", "profile", "appeals", "chat", "manager", "id", "cancel"))
    async def on_command(message: Message) -> None:
        await dispatch(message, Event.message(message.text or "/start"))

    @dispatcher.message(F.text)
    async def on_text(message: Message) -> None:
        await dispatch(message, Event.message(message.text or ""))

    @dispatcher.callback_query()
    async def on_callback(query: CallbackQuery) -> None:
        if query.message is None or query.data is None:
            return
        session = session_for(query.message)  # type: ignore[arg-type]
        outs = session.handle(Event.callback(query.data))
        await query.answer(text=next((out.notify for out in outs if out.notify), "")[:200])
        for out in outs:
            await render(query.message, str(query.message.chat.id), out)  # type: ignore[arg-type]

    # поллеру доставки нужны те же сессии и клиенты, что и обработчикам
    dispatcher.workflow_data.update(sessions=sessions, api_for=api_for)
    return dispatcher


async def deliver_loop(bot: Any, dispatcher: Any, interval: float = POLL_INTERVAL_S) -> None:
    """Доставляет клиентам то, что появилось в CRM без их участия: ответы менеджера и FINAL после INTERIM."""
    sessions: dict[str, BotSession] = dispatcher.workflow_data["sessions"]
    api_for: Callable[[str], BotApi] = dispatcher.workflow_data["api_for"]
    while True:
        for chat_key, session in list(sessions.items()):
            try:
                for item in api_for(chat_key).inbox():
                    if item.get("role") == "manager" and not session.notify_manager_replies:
                        continue  # клиент отключил уведомления об ответах менеджера в настройках
                    await bot.send_message(
                        chat_id=chat_key,
                        text=delivery_text(item),
                        reply_markup=widget_keyboard(),
                        parse_mode="HTML",
                    )
            except Exception as exc:  # одна упавшая доставка не должна останавливать цикл
                logger.error("delivery loop: %s", type(exc).__name__)
        await asyncio.sleep(interval)


async def notify_manager_loop(bot: Any, api: BotApi, manager_chats: list[int], ui_url: str = "", interval: float = MANAGER_POLL_INTERVAL_S) -> None:
    """Пересылает менеджеру уведомления CRM: спор, инъекция, «нужно решение», запрос менеджера."""
    while True:
        try:
            for note in api.manager_notifications():
                for chat in manager_chats:
                    with contextlib.suppress(Exception):
                        await bot.send_message(chat_id=chat, text=manager_notification_text(note, ui_url))
                api.mark_notification_read(int(note["id"]))
        except Exception as exc:
            logger.error("manager loop: %s", type(exc).__name__)
        await asyncio.sleep(interval)


async def run() -> int:
    """Запуск long polling: диалог бота, доставка ответов клиентам, уведомления менеджеру."""
    config = LeadBotConfig.from_env()
    if not config.ready:
        missing = [n for n, v in (("TG_LEAD_BOT_TOKEN", config.token), ("BOT_API_TOKEN", config.api_token)) if not v]
        logger.error("Не хватает переменных: %s. Проверка: python -m channels.telegram_lead_bot --check", ", ".join(missing))
        return 2
    try:
        from aiogram import Bot
    except ImportError:
        logger.error("aiogram не установлен: pip install aiogram (или pip install -r requirements.txt)")
        return 2

    # Webhook и чужой экземпляр — две причины одного TelegramConflictError, проверяем обе
    webhook = telegram_call(config.token, "getWebhookInfo").get("result") or {}
    if webhook.get("url"):
        logger.error(
            "У бота установлен webhook (%s): Telegram не отдаст апдейты в long polling. "
            "Снимите его: python -m channels.telegram_lead_bot --drop-webhook", webhook["url"],
        )
        return 3

    from aiogram.client.default import DefaultBotProperties
    from aiogram.enums import ParseMode

    # Экраны в bot_ui.py размечены HTML (<b>, <i>) — без parse_mode Telegram показывает теги как текст
    bot = Bot(token=config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    conflict = await probe_polling_free(bot)
    if conflict:
        logger.error("Бот занят другим экземпляром: %s", conflict)
        print(CONFLICT_HINT, file=sys.stderr)
        await bot.session.close()
        return 3

    await setup_commands(bot)
    me = telegram_call(config.token, "getMe").get("result") or {}
    username = me.get("username", "")
    if username:
        print(f"Бот @{username} запущен. Открыть чат: https://t.me/{username} · {START_HINT}")

    dispatcher = build_dispatcher(bot, config)
    manager_chats = parse_manager_chats(config)
    tasks = [asyncio.create_task(deliver_loop(bot, dispatcher))]
    if manager_chats:
        tasks.append(asyncio.create_task(notify_manager_loop(bot, BotApi(config), manager_chats, config.ui_url)))
    else:
        logger.warning("TG_MANAGER_CHAT_ID не задан: уведомления менеджеру в Telegram идти не будут")

    logger.info(
        "Бот запущен: API %s, менеджеры %s. Разделы меню: профиль, обращения, чат с ИИ, менеджер, помощь, каталог, настройки",
        config.api_url, manager_chats or "—",
    )
    try:
        await dispatcher.start_polling(bot)
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=True)
        await bot.session.close()
    return 0


async def check() -> int:
    """Диагностика без подключения к Telegram: настройки, бот, API и режим mock-ответов."""
    config = LeadBotConfig.from_env()
    print("Настройки бота:")
    print(f"  TG_LEAD_BOT_TOKEN:  {'задан' if config.token else 'НЕ задан — запуск невозможен'}")
    print(f"  BOT_API_TOKEN:      {'задан' if config.api_token else 'НЕ задан — /api/bot/* ответит 503'}")
    print(f"  API_URL:            {config.api_url}")
    print(f"  TG_MANAGER_CHAT_ID: {config.manager_chat_id or 'не задан (уведомления менеджеру в Telegram не пойдут)'}")
    print(f"  UI_URL:             {config.ui_url or 'не задан (ссылка на дашборд в уведомлениях будет короче)'}")
    # Команды и разделы печатаем даже без токена: так видно, какая версия диалога запущена
    print(f"  Команды бота:       {' '.join('/' + name for name, _ in BOT_COMMANDS)}")
    print(f"  Разделы виджета:    {' · '.join(label for row in ui.widget_menu() for label in row)}")
    print(f"  Клиенту:            {START_HINT}")
    if config.token:
        # Диагностика Telegram: какой это бот и не мешает ли polling установленный webhook
        me = telegram_call(config.token, "getMe")
        if me.get("ok"):
            username = (me.get("result") or {}).get("username", "")
            print(f"  Бот в Telegram:     @{username or '?'}" + (f" — открыть https://t.me/{username}" if username else ""))
        else:
            print(f"  Бот в Telegram:     токен не принят — {me.get('description', '')}")
            return 1
        webhook = telegram_call(config.token, "getWebhookInfo").get("result") or {}
        if webhook.get("url"):
            print(f"  Webhook:            УСТАНОВЛЕН ({webhook['url']}) — polling будет конфликтовать")
            print("                      снять: python -m channels.telegram_lead_bot --drop-webhook")
        else:
            print("  Webhook:            не установлен (можно работать через long polling)")

    api = BotApi(config)
    try:
        health = api.health()
    except BotError as exc:
        print(f"  API:                НЕ отвечает — {exc}")
        return 1
    llm = health.get("provider", "?")
    print(f"  API:                отвечает — статус {health.get('status', '?')}, политика v{health.get('policy_version', '?')}")
    print(f"  LLM:                провайдер {llm}" + (" (ключей нет — ответы детерминированные, этого достаточно для демо)" if llm in ("mock", "") else ""))
    print("  Офлайн-прогон:      python -m channels.mock_telegram   (весь диалог без Telegram и без CRM)")
    if not config.ready:
        return 1
    return 0


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Telegram-бот AI-native CRM: меню, обращения, чат с ИИ")
    parser.add_argument("--check", action="store_true", help="проверить настройки, бота в Telegram и доступность API")
    parser.add_argument("--mock", action="store_true", help="прогнать диалог в терминале без Telegram (mock-CRM)")
    parser.add_argument("--drop-webhook", action="store_true",
                        help="снять webhook и очистить накопившиеся апдейты (лечит TelegramConflictError)")
    args = parser.parse_args()
    if args.drop_webhook:
        config = LeadBotConfig.from_env()
        if not config.token:
            print("TG_LEAD_BOT_TOKEN не задан — снимать нечего. Проверьте .env.")
            raise SystemExit(2)
        raise SystemExit(drop_webhook(config))
    if args.mock:
        from channels import mock_telegram  # локальный импорт: mock-режим не нужен в бою

        raise SystemExit(mock_telegram.main([]))
    raise SystemExit(asyncio.run(check() if args.check else run()))


if __name__ == "__main__":
    main()
