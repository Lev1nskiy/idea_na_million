"""Telegram-бот-анкета: собирает данные о человеке и отдаёт их в CRM.

Отдельный бот от клиентского чата (`channels/telegram_bot.py`): здесь нет ИИ-движка и
переписки, только анкета из пяти вопросов. Собранные данные уходят на бэкенд
(`POST /api/leads`), где создаётся или обновляется карточка клиента: она сразу видна сайту
в разделе клиентов и в ленте уведомлений менеджера, а дальше используется в работе —
в том числе клиентским чат-ботом, который ищет клиента по `telegram_id`.

Запуск:
    python -m channels.telegram_lead_bot --check   # проверить настройки, ничего не отправляя
    python -m channels.telegram_lead_bot           # начать приём заявок (long polling)

Переменные окружения:
    TG_LEAD_BOT_TOKEN   — токен бота от @BotFather (обязателен для запуска)
    BOT_API_TOKEN       — сервисный токен для /api/leads (тот же, что в .env бэкенда)
    API_URL             — адрес API (по умолчанию http://localhost:8000)
    TG_MANAGER_CHAT_ID  — чат менеджера: туда уходит копия каждой заявки (необязательно)
    UI_URL              — адрес дашборда для ссылки в уведомлении (необязательно)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from channels.lead_flow import (
    BYE,
    GREETING,
    RESTART,
    SKIP_NOTE,
    LeadSession,
    accept,
    as_lead,
    confirm_done,
    is_cancel,
    prompt,
    summary,
)

logger = logging.getLogger(__name__)

CHANNEL = "telegram-lead"
API_TIMEOUT_S = 10.0


@dataclass
class LeadBotConfig:
    token: str
    api_url: str
    api_token: str
    manager_chat_id: str = ""
    ui_url: str = ""

    @classmethod
    def from_env(cls) -> LeadBotConfig:
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


class LeadApi:
    """Клиент /api/leads. Ничего, кроме отправки анкеты, боту не нужно."""

    def __init__(self, config: LeadBotConfig) -> None:
        self._config = config

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._config.api_url}{path}",
            method=method,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json", "X-Bot-Token": self._config.api_token},
        )
        try:
            with urllib.request.urlopen(request, timeout=API_TIMEOUT_S) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"API {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"API недоступен ({exc.reason})") from exc

    def submit(self, lead: Any) -> dict[str, Any]:
        """Отправляет анкету. Возвращает отчёт сервера (client_id, created, changed)."""
        return self._call(
            "POST",
            "/api/leads",
            {
                "name": lead.name,
                "company": lead.company,
                "phone": lead.phone,
                "email": lead.email,
                "request": lead.request,
                "telegram_id": lead.telegram_id,
                "telegram_username": lead.telegram_username,
                "source": lead.source,
            },
        )

    def health(self) -> dict[str, Any]:
        """Проверка доступности API для `--check` (публичный health, без токена)."""
        payload = self._call("GET", "/api/health")
        return payload if isinstance(payload, dict) else {}


def telegram_call(token: str, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Вызов Telegram Bot API — нужен только диагностике (getMe, getWebhookInfo, deleteWebhook).

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


# --------------------------------------------------------------------------- диалог

#: Состояния: NONE (ждём /start) → FILL (идёт анкета) → CONFIRM (ждём подтверждения)
_sessions: dict[str, LeadSession] = {}
_state: dict[str, str] = {}


def _chat_key(chat_id: Any) -> str:
    return str(chat_id)


def handle_command(text: str, session: LeadSession, state: str) -> tuple[str, str, bool]:
    """Обрабатывает команды и подтверждение.

    Возвращает (ответ клиенту, новое состояние, нужно ли отправлять анкету на сервер).
    Вынесено из aiogram-обвязки, чтобы проверяться тестами без Telegram.
    """
    command = text.strip().lower()
    if command in {"/start", "start", "начать", "заявка"}:
        session.reset()
        return GREETING + "\n\n" + prompt(session), "FILL", False
    if is_cancel(command):
        session.reset()
        return BYE, "NONE", False
    if state == "CONFIRM":
        if command in {"да", "yes", "ок", "ok", "верно", "+"}:
            return "", "SENDING", True
        if command in {"заново", "restart", "исправить", "нет", "no"}:
            session.reset()
            return RESTART + "\n\n" + prompt(session), "FILL", False
        return "Ответьте «да» — отправляем, или «заново» — заполнить анкету снова.", "CONFIRM", False
    return "", state, False


def handle_text(text: str, session: LeadSession) -> tuple[str, str | None]:
    """Шаг анкеты: возвращает (ответ боту, ошибка ввода) — ошибка не двигает прогресс."""
    error = accept(session, text)
    if error:
        return error, error
    if session.finished:
        return summary(session), None
    if text.strip().lower() in {"-", "—", "нет", "пропустить", "skip", "не хочу"}:
        return SKIP_NOTE + "\n\n" + prompt(session), None
    return prompt(session), None


def build_lead_from(session: LeadSession) -> Any:
    """Анкета → Lead (валидация теми же правилами, что и на сервере)."""
    return as_lead(session)


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


def manager_message(report: dict[str, Any], lead: Any, ui_url: str = "") -> str:
    """Копия заявки менеджеру в Telegram (без телефона/почты — они в карточке CRM)."""
    who = lead.company or lead.name
    need = f"\nЗадача: {lead.request}" if lead.request else ""
    link = f"\nДашборд: {ui_url}/dashboard" if ui_url else ""
    kind = "Новая заявка" if report.get("created") else "Обновление по клиенту"
    return f"{kind} #{report.get('client_id')}: {who}{need}\nКонтакт: {lead.telegram_username or lead.telegram_id}{link}"


# --------------------------------------------------------------------------- aiogram

def build_dispatcher(bot: Any, api: LeadApi, config: LeadBotConfig) -> Any:
    """Собирает aiogram-роутер поверх чистой логики (см. handle_command/handle_text)."""
    from aiogram import F, Router
    from aiogram.filters import Command
    from aiogram.types import Message

    router = Router()

    async def reply(message: Message, text: str) -> None:
        if text:
            await message.answer(text)

    async def submit(message: Message, session: LeadSession) -> None:
        """Отправка анкеты на сервер и ответ клиенту."""
        try:
            lead = build_lead_from(session)
        except Exception as exc:  # LeadError и прочее: показываем причину и даём исправиться
            _state[_chat_key(message.chat.id)] = "FILL"
            session.index = len(session.answers)  # возвращаемся к незаполненному шагу
            await reply(message, f"Не получилось собрать заявку: {exc}\nНапишите /start, чтобы начать заново.")
            return
        try:
            report = api.submit(lead)
        except RuntimeError as exc:
            logger.error("Заявка не ушла: %s", exc)
            _state[_chat_key(message.chat.id)] = "CONFIRM"
            await reply(message, "Не получилось передать заявку — техническая неполадка. Попробуйте ещё раз: «да».")
            return
        _state.pop(_chat_key(message.chat.id), None)
        session.reset()
        await reply(message, confirm_done(int(report.get("client_id", 0)), lead))
        for chat in parse_manager_chats(config):
            try:
                await bot.send_message(chat, manager_message(report, lead, config.ui_url))
            except Exception as exc:  # noqa: BLE001 — менеджер не должен ломать ответ клиенту
                logger.warning("Не смог уведомить менеджера %s: %s", chat, exc)

    @router.message(Command("start", "cancel", "stop"))
    async def on_command(message: Message) -> None:  # noqa: D401
        key = _chat_key(message.chat.id)
        session = _sessions.setdefault(key, _make_session(message))
        text = message.text or ""
        answer, state, should_send = handle_command(text, session, _state.get(key, "NONE"))
        _state[key] = state
        await reply(message, answer)
        if should_send:
            await submit(message, session)

    @router.message(F.text)
    async def on_text(message: Message) -> None:
        key = _chat_key(message.chat.id)
        session = _sessions.setdefault(key, _make_session(message))
        state = _state.get(key, "NONE")
        text = message.text or ""
        if state == "NONE":
            await reply(message, "Привет! Чтобы оставить заявку, напишите /start.")
            return
        answer, state_new, should_send = handle_command(text, session, state)
        if state_new != state and state != "CONFIRM":
            _state[key] = state_new
        if should_send:
            _state[key] = "SENDING"
            await submit(message, session)
            return
        if answer:  # команда/подтверждение уже дали текст
            await reply(message, answer)
            return
        answer, error = handle_text(text, session)
        if not error and session.finished:
            _state[key] = "CONFIRM"
        await reply(message, answer)

    return router


def _make_session(message: Any) -> LeadSession:
    """Запоминаем telegram_id/username сразу: они и есть «контакт» по умолчанию."""
    user = getattr(message, "from_user", None)
    return LeadSession(
        telegram_id=str(user.id) if user else None,
        telegram_username=getattr(user, "username", None),
    )


async def run() -> int:
    """Запуск long polling. Требует установленного aiogram."""
    config = LeadBotConfig.from_env()
    if not config.ready:
        missing = [name for name, value in (("TG_LEAD_BOT_TOKEN", config.token), ("BOT_API_TOKEN", config.api_token)) if not value]
        logger.error("Не хватает переменных: %s. Проверка: python -m channels.telegram_lead_bot --check", ", ".join(missing))
        return 2
    try:
        from aiogram import Bot, Dispatcher
    except ImportError:
        logger.error("aiogram не установлен: pip install aiogram (или pip install -r requirements.txt)")
        return 2

    # Webhook и чужой экземпляр — две причины одного и того же TelegramConflictError,
    # поэтому проверяем оба до старта: понятная подсказка лучше бесконечных повторов aiogram.
    webhook = telegram_call(config.token, "getWebhookInfo").get("result") or {}
    if webhook.get("url"):
        logger.error(
            "У бота установлен webhook (%s): Telegram не отдаст апдейты в long polling. "
            "Снимите его: python -m channels.telegram_lead_bot --drop-webhook", webhook["url"],
        )
        return 3

    bot = Bot(token=config.token)
    conflict = await probe_polling_free(bot)
    if conflict:
        logger.error("Бот занят другим экземпляром: %s", conflict)
        print(CONFLICT_HINT, file=sys.stderr)
        await bot.session.close()
        return 3

    dispatcher = Dispatcher()
    dispatcher.include_router(build_dispatcher(bot, LeadApi(config), config))
    logger.info("Бот-анкета запущен: API %s, канал %s", config.api_url, CHANNEL)
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
    return 0


async def check() -> int:
    """Диагностика без подключения к Telegram: настройки и доступность API."""
    config = LeadBotConfig.from_env()
    print("Настройки бота-анкеты:")
    print(f"  TG_LEAD_BOT_TOKEN:  {'задан' if config.token else 'НЕ задан — запуск невозможен'}")
    print(f"  BOT_API_TOKEN:      {'задан' if config.api_token else 'НЕ задан — /api/leads ответит 503'}")
    print(f"  API_URL:            {config.api_url}")
    print(f"  TG_MANAGER_CHAT_ID: {config.manager_chat_id or 'не задан (копии заявок не уйдут)'}")
    if config.token:
        # Диагностика Telegram: какой это бот и не мешает ли polling установленный webhook
        me = telegram_call(config.token, "getMe")
        if me.get("ok"):
            username = (me.get("result") or {}).get("username", "?")
            print(f"  Бот в Telegram:     @{username}")
        else:
            print(f"  Бот в Telegram:     токен не принят — {me.get('description', '')}")
            return 1
        webhook = (telegram_call(config.token, "getWebhookInfo").get("result") or {})
        if webhook.get("url"):
            print(f"  Webhook:            УСТАНОВЛЕН ({webhook['url']}) — polling будет конфликтовать")
            print("                      снять: python -m channels.telegram_lead_bot --drop-webhook")
        else:
            print("  Webhook:            не установлен (можно работать через long polling)")

    api = LeadApi(config)
    try:
        health = api.health()
        print(f"  API:                отвечает — {health.get('status', '?')}, провайдер {health.get('provider', '?')}")
    except RuntimeError as exc:
        print(f"  API:                НЕ отвечает — {exc}")
        return 1
    if not config.ready:
        return 1
    return 0


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Telegram-бот-анкета AI-native CRM")
    parser.add_argument("--check", action="store_true", help="проверить настройки, бота в Telegram и доступность API")
    parser.add_argument("--drop-webhook", action="store_true",
                        help="снять webhook и очистить накопившиеся апдейты (лечит TelegramConflictError)")
    args = parser.parse_args()
    if args.drop_webhook:
        config = LeadBotConfig.from_env()
        if not config.token:
            print("TG_LEAD_BOT_TOKEN не задан — снимать нечего. Проверьте .env.")
            raise SystemExit(2)
        raise SystemExit(drop_webhook(config))
    raise SystemExit(asyncio.run(check() if args.check else run()))


if __name__ == "__main__":
    main()
