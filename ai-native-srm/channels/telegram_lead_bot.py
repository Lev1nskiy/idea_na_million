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
import urllib.error
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

    bot = Bot(token=config.token)
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
    parser.add_argument("--check", action="store_true", help="проверить настройки и API, не подключаясь к Telegram")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(check() if args.check else run()))


if __name__ == "__main__":
    main()
