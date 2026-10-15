"""Тесты Telegram-адаптера: логика без aiogram (фейковый ApiClient) + сборка Dispatcher.

Проверяем именно то, что отличает адаптер от ядра: привязку чата к клиенту,
доставку ответов, отсутствие дублей в уведомлениях и «непривязанный» сценарий.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from channels import telegram_bot as tb


class FakeApi:
    """Подмена ApiClient: считает вызовы и отдаёт заранее заданные данные."""

    def __init__(
        self,
        *,
        bindings: dict[str, int] | None = None,
        replies: list[dict[str, Any]] | None = None,
        outbox: list[dict[str, Any]] | None = None,
        notifications: list[dict[str, Any]] | None = None,
        conversations: dict[int, int] | None = None,
        telegram_ids: dict[int, str | None] | None = None,
    ) -> None:
        self.bindings = bindings or {}
        # именно `is None`, а не `or`: пустой список — валидный кейс «ядро не вернуло текста»
        self.replies = [{"text": "ответ ИИ"}] if replies is None else replies
        self.outbox = outbox or []
        self.notifications_feed = notifications or []
        self.conversations = conversations or {}
        self.telegram_ids = telegram_ids or {}
        self.sent: list[tuple[int, str]] = []
        self.read: list[int] = []
        self.outbox_calls = 0

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "provider": "fake", "policy_version": "1"}

    async def client_by_telegram(self, telegram_id: str) -> int | None:
        return self.bindings.get(telegram_id)

    async def send_message(self, client_id: int, text: str, conversation_id: int | None = None) -> dict[str, Any]:
        self.sent.append((client_id, text))
        return {"conversation_id": 42, "messages": self.replies}

    async def pull_outbox(self, channel: str = tb.CHANNEL) -> list[dict[str, Any]]:
        self.outbox_calls += 1
        items, self.outbox = self.outbox, []  # отдаём один раз, как настоящий outbox
        return items

    async def notifications(self, unread_only: bool = True) -> list[dict[str, Any]]:
        return self.notifications_feed

    async def mark_notification_read(self, notification_id: int) -> None:
        self.read.append(notification_id)

    async def conversation_client(self, conversation_id: int) -> int | None:
        return self.conversations.get(conversation_id)

    async def client_telegram_id(self, client_id: int) -> str | None:
        return self.telegram_ids.get(client_id)


# --- Клиентский путь ------------------------------------------------------------------------
def test_bound_client_message_goes_to_engine_and_answer_returned():
    api = FakeApi(bindings={"555": 4}, replies=[{"text": "Ответ по заказу"}, {"text": "Второе сообщение"}])
    texts = asyncio.run(tb.process_client_text(api, "555", "Где мой заказ?"))

    assert texts == ["Ответ по заказу", "Второе сообщение"]
    assert api.sent == [(4, "Где мой заказ?")]  # текст ушёл в ядро как есть, без обработки ботом


def test_unbound_telegram_gets_hint_without_touching_engine():
    api = FakeApi()
    texts = asyncio.run(tb.process_client_text(api, "999", "Привет"))

    assert texts == [tb.UNKNOWN_CLIENT_HINT]
    assert api.sent == []  # бизнес-логика не запускалась


def test_demo_client_mode_answers_from_demo_client():
    api = FakeApi(bindings={}, replies=[{"text": "Демо-ответ"}])
    texts = asyncio.run(tb.process_client_text(api, "999", "Привет", demo_client_id=1))

    assert texts == ["Демо-ответ"]
    assert api.sent == [(1, "Привет")]


def test_empty_engine_answer_gets_interim_hint():
    api = FakeApi(bindings={"1": 1}, replies=[])
    assert asyncio.run(tb.process_client_text(api, "1", "текст")) == [tb.EMPTY_ANSWER_HINT]


# --- Доставка outbox ------------------------------------------------------------------------
def test_outbox_delivered_to_chat_resolved_through_binding():
    api = FakeApi(
        outbox=[
            {"conversation_id": 7, "text": "Первый ответ"},
            {"conversation_id": 7, "text": "Второй ответ"},
            {"conversation_id": 8, "text": "Ответ другого клиента"},
        ],
        conversations={7: 4, 8: 5},
        telegram_ids={4: "555", 5: None},  # у клиента 5 нет Telegram — доставлять некуда
    )
    pairs = asyncio.run(tb.deliver_outbox(api))

    assert pairs == [("555", "Первый ответ"), ("555", "Второй ответ")]
    assert api.outbox_calls == 1


def test_outbox_without_admin_token_is_not_delivered():
    """Без ADMIN_TOKEN telegram_id не резолвится — сообщения не улетают «наугад»."""
    api = FakeApi(outbox=[{"conversation_id": 7, "text": "Ответ"}], conversations={7: 4})
    assert asyncio.run(tb.deliver_outbox(api)) == []


# --- Уведомления менеджеру -------------------------------------------------------------------
def test_manager_notification_text_includes_escalation_details():
    note = {
        "id": 3, "type": "DISPUTE", "client_name": "Елена Попова", "waited_min": 27,
        "reminder_level": 2, "conversation_id": 12,
        "text": "Клиент ждёт менеджера 27 мин (напоминание 2) — срок SLA просрочен",
    }
    text = tb.manager_notification_text(note, ui_url="http://localhost:8501")

    assert "СПОР" in text and "Елена Попова" in text
    assert "ждёт 27 мин" in text and "напоминание 2" in text
    assert "просрочен" in text and "#12" in text


def test_unknown_notification_type_has_default_title():
    assert tb.manager_notification_text({"type": "REMINDER", "text": "текст"}).startswith("🔔 НАПОМИНАНИЕ")


def test_collect_marks_read_and_returns_text_per_notification():
    api = FakeApi(
        notifications=[
            {"id": 1, "type": "SECURITY", "text": "Попытка инъекции", "conversation_id": 5},
            {"id": 2, "type": "OVERRIDE_DUE", "text": "Нужно уточнение", "conversation_id": 6},
        ]
    )
    texts = asyncio.run(tb.collect_manager_notifications(api))

    assert len(texts) == 2 and api.read == [1, 2]  # повторно не отправим


# --- Конфигурация и обвязка aiogram ----------------------------------------------------------
def test_config_from_env_reads_demo_client_and_urls(monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TG_MANAGER_CHAT_ID", "-100500")
    monkeypatch.setenv("API_URL", "http://api:8000/")
    monkeypatch.setenv("ADMIN_TOKEN", "secret")
    monkeypatch.setenv("TG_DEMO_CLIENT_ID", "4")

    config = tb.TelegramConfig.from_env()

    assert config.token == "123:abc" and config.manager_chat_id == "-100500"
    assert config.api_url == "http://api:8000" and config.admin_token == "secret" and config.demo_client_id == 4


def test_config_ignores_non_numeric_demo_client(monkeypatch):
    monkeypatch.setenv("TG_DEMO_CLIENT_ID", "нет")
    assert tb.TelegramConfig.from_env().demo_client_id is None


def test_build_dispatcher_registers_handlers_without_network():
    """Dispatcher собирается с фиктивным токеном: сеть не трогаем, обработчики на месте."""
    aiogram = pytest.importorskip("aiogram")

    from aiogram import Bot

    bot = Bot(token="123456:AAaaAAaaAAaaAAaaAAaaAAaaAAaaAAaaAAa")
    config = tb.TelegramConfig(token="123456:AAaaAAaaAAaaAAaaAAaaAAaaAAaaAAaaAAa", manager_chat_id="", api_url="http://localhost:8000", admin_token="", demo_client_id=None)
    dp = tb.build_dispatcher(bot, FakeApi(), config)

    assert aiogram.__version__
    assert len(dp.message.handlers) == 2  # /start|/help и текстовый обработчик
    asyncio.run(bot.session.close())


def test_check_reports_missing_api_and_token(monkeypatch, capsys):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")  # заведомо закрытый порт
    code = asyncio.run(tb.check())
    out = capsys.readouterr().out

    assert code == 1
    assert "НЕ ЗАДАН" in out and "НЕДОСТУПЕН" in out


def test_bound_clients_requires_admin_token():
    """Без админ-токена привязки не читаются — не ходим в API зря (Ст. 4)."""
    api = tb.ApiClient("http://localhost:8000", admin_token="")
    assert asyncio.run(api.bound_clients()) == []


def test_bound_clients_iterates_clients_and_keeps_only_bound(monkeypatch):
    api = tb.ApiClient("http://localhost:8000", admin_token="secret")
    seen: list[tuple[str, str]] = []

    async def fake_request(method: str, path: str, **kwargs: Any) -> Any:
        seen.append((method, path))
        if path == "/api/clients":
            return [{"id": 4}, {"id": 5}]
        return {"id": 5, "telegram_id": "555" if path.endswith("/5") else None}

    monkeypatch.setattr(api, "_request", fake_request)

    assert asyncio.run(api.bound_clients()) == [5]
    assert seen[0] == ("GET", "/api/clients")
