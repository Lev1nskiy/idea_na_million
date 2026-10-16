"""Тесты Telegram-бота: анкета-основа, настройки, HTTP-клиент и диагностика.

Сам диалог бота (меню, профиль, обращения, чат с ИИ, вызов менеджера) проверяется в
`tests/test_bot_flow.py`, фасад `/api/bot/*` — в `tests/test_bot_api.py`. Здесь — то, что
вокруг диалога: чистая анкета (`channels/lead_flow.py`), токены и настройки, вызовы
Telegram Bot API и подсказки при конфликте экземпляров.
"""
from __future__ import annotations

import asyncio
import io
import json

import pytest

from channels import lead_flow as lf
from channels import telegram_lead_bot as tlb
from core.leads import LeadError


def _fill(session: lf.LeadSession, values: list[str]) -> None:
    for value in values:
        assert lf.accept(session, value) is None


# --- Анкета: шаги, пропуски, ошибки -----------------------------------------------------------

def test_questions_cover_base_fields_and_request_last():
    keys = [q.key for q in lf.QUESTIONS]
    assert keys == ["name", "company", "phone", "email", "request"]
    assert lf.QUESTIONS[0].required  # без имени менеджер не знает, с кем говорить
    assert not any(q.required for q in lf.QUESTIONS[1:])


def test_full_flow_builds_lead_with_normalized_phone():
    session = lf.LeadSession(telegram_id="555", telegram_username="dm")
    _fill(session, ["Дмитрий Соколов", "ООО Ромашка", "8 (999) 123-45-67", "zakaz@romashka.ru", "10 принтеров LP-220"])

    assert session.finished
    lead = lf.as_lead(session)

    assert lead.name == "Дмитрий Соколов"
    assert lead.phone == "+79991234567"  # «8 (999) …» приводим к одному виду
    assert lead.company == "ООО Ромашка"
    assert lead.source == "telegram_lead_bot"
    assert lead.telegram_id == "555"


def test_optional_fields_can_be_skipped_and_request_is_optional():
    session = lf.LeadSession(telegram_id="556")
    _fill(session, ["Анна", "-", "-", "-", "нужен расчёт"])

    lead = lf.as_lead(session)
    assert lead.company is None and lead.phone is None and lead.email is None
    assert lead.request == "нужен расчёт"


def test_required_question_rejects_skip_and_empty():
    session = lf.LeadSession()
    assert "обязательный" in lf.accept(session, "-")
    assert "Пустое" in lf.accept(session, "   ")
    assert session.index == 0  # шаг не сдвинулся


def test_bad_phone_is_reported_and_step_repeats():
    session = lf.LeadSession()
    _fill(session, ["Пётр", "ООО Тест"])
    error = lf.accept(session, "спросите у секретаря")

    assert error and "не распознан" in error
    assert session.index == 2
    _fill(session, ["+7 495 000-11-22", "-", "заявка на сервис"])


def test_short_request_is_rejected_by_validation():
    session = lf.LeadSession(telegram_id="557")
    _fill(session, ["Иван", "-", "+79001112233", "-", "аб"])
    with pytest.raises(LeadError):
        lf.as_lead(session)


def test_progress_and_prompt_text_show_step_number():
    session = lf.LeadSession()
    assert "1 из 5" in lf.prompt(session)
    assert "Как вас зовут" in lf.prompt(session)
    session.index = 2
    assert "Можно пропустить" in lf.prompt(session)


def test_cancel_detection_accepts_russian_and_latin():
    assert lf.is_cancel("Отмена") and lf.is_cancel("/stop")
    assert not lf.is_cancel("нет, всё верно")


def test_summary_lists_all_fields_with_dashes():
    session = lf.LeadSession()
    _fill(session, ["Ольга", "-", "+79990001122", "-", "консультация"])
    text = lf.summary(session)
    assert "Ольга" in text and "Компания: —" in text and "консультация" in text


def test_confirm_done_mentions_card_number_and_channel():
    session = lf.LeadSession(telegram_id="5")
    _fill(session, ["Сергей", "ООО Тест", "+79990001122", "-", "заявка"])
    text = lf.confirm_done(1007, lf.as_lead(session))
    assert "№1007" in text and "ООО Тест" in text and "+79990001122" in text


# --- Конфигурация и обвязка aiogram -----------------------------------------------------------

def test_config_from_env_requires_both_tokens(monkeypatch):
    monkeypatch.setenv("TG_LEAD_BOT_TOKEN", "123:abc")
    monkeypatch.delenv("BOT_API_TOKEN", raising=False)
    config = tlb.LeadBotConfig.from_env()
    assert config.token == "123:abc" and not config.ready

    monkeypatch.setenv("BOT_API_TOKEN", "secret")
    assert tlb.LeadBotConfig.from_env().ready


def test_manager_chats_parses_list_and_skips_garbage():
    config = tlb.LeadBotConfig(token="t", api_url="u", api_token="s", manager_chat_id="111, 222 ,abc")
    assert tlb.parse_manager_chats(config) == [111, 222]


def test_dispatcher_registers_handlers_widget_and_inline_keys():
    """Сборка aiogram-обвязки без сети: обработчики, виджет-меню и inline-кнопки."""
    pytest.importorskip("aiogram")
    from aiogram import Bot

    from channels import bot_ui as ui

    config = tlb.LeadBotConfig(
        token="123456:AAaaAAaaAAaaAAaaAAaaAAaaAAaaAAaaAAa", api_url="http://localhost:8000", api_token="secret"
    )
    bot = Bot(token=config.token)
    try:
        dispatcher = tlb.build_dispatcher(bot, config)
        assert len(dispatcher.message.handlers) == 2  # команды и любой текст
        assert len(dispatcher.callback_query.handlers) == 1  # нажатия inline-кнопок

        widget = tlb.widget_keyboard()
        assert [button.text for row in widget.keyboard for button in row] == [
            label for row in ui.widget_menu() for label in row
        ]
        inline = tlb.inline_keyboard(ui.help_screen(has_profile=True).rows)
        assert inline is not None and inline.inline_keyboard[0][0].text
    finally:
        asyncio.run(bot.session.close())


def test_delivery_text_marks_manager_and_ai_messages():
    assert tlb.delivery_text({"role": "manager", "text": "Замена согласована"}).startswith("🧑‍💼")
    assert tlb.delivery_text({"role": "ai", "text": "Уточняю"}).startswith("🤖")
    assert tlb.delivery_text({"text": "Сообщение"}).startswith("Сообщение")


def test_check_reports_missing_settings_without_network(monkeypatch, capsys):
    monkeypatch.delenv("TG_LEAD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_API_TOKEN", raising=False)
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")  # заведомо закрытый порт
    code = asyncio.run(tlb.check())
    out = capsys.readouterr().out
    assert code == 1
    assert "НЕ задан" in out and "НЕ отвечает" in out


def test_bot_api_sends_token_and_telegram_id(monkeypatch):
    """Проверяем сам HTTP-вызов: заголовок X-Bot-Token и telegram_id в запросе, а не в тексте клиента."""
    captured: dict = {}

    class FakeResponse:
        def read(self) -> bytes:
            return json.dumps({"found": True, "client": {"name": "Дмитрий"}}, ensure_ascii=False).encode()

        def __enter__(self):  # noqa: ANN204 — протокол urlopen
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    def fake_urlopen(request, timeout=None):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        captured["body"] = json.loads(request.data.decode()) if request.data else None
        return FakeResponse()

    monkeypatch.setattr(tlb.urllib.request, "urlopen", fake_urlopen)
    api = tlb.BotApi(
        tlb.LeadBotConfig(token="t", api_url="http://api.local", api_token="secret"),
        telegram_id="555", display_name="Дмитрий",
    )

    assert api.profile()["name"] == "Дмитрий"
    assert captured["url"].startswith("http://api.local/api/bot/me?") and "telegram_id=555" in captured["url"]
    assert captured["headers"]["x-bot-token"] == "secret"

    api.chat("привет")
    assert captured["url"] == "http://api.local/api/bot/messages"
    assert captured["body"] == {
        "telegram_id": "555", "display_name": "Дмитрий", "text": "привет", "conversation_id": None
    }


def test_bot_api_speaks_human_language_on_errors(monkeypatch):
    """Ошибки CRM превращаем в понятный текст: его увидит клиент в чате."""
    from channels.bot_flow import BotError

    api = tlb.BotApi(tlb.LeadBotConfig(token="t", api_url="http://api.local", api_token="secret"), telegram_id="555")

    def bad_token(request, timeout=None):  # noqa: ANN001
        raise tlb.urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO('{"detail": "неверный X-Bot-Token"}'.encode())
        )

    monkeypatch.setattr(tlb.urllib.request, "urlopen", bad_token)
    with pytest.raises(BotError, match="токен"):
        api.profile()

    def offline(request, timeout=None):  # noqa: ANN001
        raise tlb.urllib.error.URLError("connection refused")

    monkeypatch.setattr(tlb.urllib.request, "urlopen", offline)
    with pytest.raises(BotError, match="недоступна"):
        api.appeals()


# --- Диагностика Telegram: чужой экземпляр и webhook -------------------------------------------

def test_telegram_call_turns_errors_into_payload(monkeypatch):
    """Диагностика не должна падать сама: ошибку Telegram показываем текстом."""

    def fail(request, timeout=None):
        raise tlb.urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(tlb.urllib.request, "urlopen", fail)
    result = tlb.telegram_call("bad-token", "getMe")
    assert result["ok"] is False and "401" in result["description"]


def test_drop_webhook_reports_success(monkeypatch, capsys):
    calls = []

    def fake_call(token, method, params=None):
        calls.append((method, params))
        return {"ok": True, "result": True}

    monkeypatch.setattr(tlb, "telegram_call", fake_call)
    code = tlb.drop_webhook(tlb.LeadBotConfig(token="t", api_url="u", api_token="s"))

    assert code == 0
    assert calls == [("deleteWebhook", {"drop_pending_updates": "true"})]
    assert "Webhook снят" in capsys.readouterr().out


def test_drop_webhook_explains_bad_token(monkeypatch, capsys):
    monkeypatch.setattr(tlb, "telegram_call", lambda *a, **k: {"ok": False, "description": "Unauthorized"})
    code = tlb.drop_webhook(tlb.LeadBotConfig(token="t", api_url="u", api_token="s"))

    assert code == 1
    assert "TG_LEAD_BOT_TOKEN" in capsys.readouterr().out


def test_check_shows_bot_and_warns_about_webhook(monkeypatch, capsys):
    """--check должен предупредить про webhook: с ним long polling не заработает."""
    monkeypatch.setenv("TG_LEAD_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("BOT_API_TOKEN", "secret")
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")

    def fake_call(token, method, params=None):
        if method == "getMe":
            return {"ok": True, "result": {"username": "los_zayavka_bot"}}
        return {"ok": True, "result": {"url": "https://example.com/hook"}}

    monkeypatch.setattr(tlb, "telegram_call", fake_call)
    code = asyncio.run(tlb.check())
    out = capsys.readouterr().out

    assert code == 1  # API заведомо недоступен — это отдельная проверка
    assert "@los_zayavka_bot" in out
    assert "Webhook:            УСТАНОВЛЕН" in out and "--drop-webhook" in out


def test_check_says_webhook_is_free(monkeypatch, capsys):
    monkeypatch.setenv("TG_LEAD_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("BOT_API_TOKEN", "secret")
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")

    def fake_call(token, method, params=None):
        if method == "getMe":
            return {"ok": True, "result": {"username": "los_zayavka_bot"}}
        return {"ok": True, "result": {}}

    monkeypatch.setattr(tlb, "telegram_call", fake_call)
    asyncio.run(tlb.check())
    assert "не установлен" in capsys.readouterr().out


def test_check_rejects_revoked_token(monkeypatch, capsys):
    monkeypatch.setenv("TG_LEAD_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("BOT_API_TOKEN", "secret")
    monkeypatch.setattr(tlb, "telegram_call", lambda *a, **k: {"ok": False, "description": "Unauthorized"})

    assert asyncio.run(tlb.check()) == 1
    assert "токен не принят" in capsys.readouterr().out


def test_probe_polling_free_reports_conflict():
    """Чужой экземпляр/вебхук: getUpdates падает конфликтом — бот это увидит и объяснит."""
    pytest.importorskip("aiogram")
    from aiogram.exceptions import TelegramConflictError

    class FakeBot:
        async def get_updates(self, **kwargs):
            raise TelegramConflictError(method=None, message="Conflict: terminated by other getUpdates request")

    assert "Conflict" in (asyncio.run(tlb.probe_polling_free(FakeBot())) or "")


def test_probe_polling_free_is_silent_when_free():
    pytest.importorskip("aiogram")

    class FakeBot:
        async def get_updates(self, **kwargs):
            return []

    assert asyncio.run(tlb.probe_polling_free(FakeBot())) is None


def test_conflict_hint_has_ready_commands():
    """Подсказка должна содержать готовые команды, а не «запущено два экземпляра»."""
    assert "Stop-Process" in tlb.CONFLICT_HINT
    assert "--drop-webhook" in tlb.CONFLICT_HINT
    assert "telegram_lead_bot" in tlb.CONFLICT_HINT


# --- Диагностика канала: разбор ответа getUpdates ---------------------------------------------

def test_conflict_reason_recognises_other_instance():
    from scripts.check_telegram import conflict_reason

    probe = {"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates request"}
    assert "конфликт" in conflict_reason(probe)


def test_conflict_reason_recognises_webhook_and_bad_token():
    from scripts.check_telegram import conflict_reason

    assert "webhook" in conflict_reason({"ok": False, "error_code": 409,
                                        "description": "Conflict: can't use getUpdates method while webhook is active"})
    assert "токен не принят" in conflict_reason({"ok": False, "error_code": 401, "description": "Unauthorized"})


def test_conflict_reason_is_silent_when_free():
    from scripts.check_telegram import conflict_reason

    assert conflict_reason({"ok": True, "result": []}) is None


def test_check_telegram_reads_env_file(tmp_path, monkeypatch):
    """Диагностика должна брать токены из .env — иначе запускать её пришлось бы с переменными."""
    from scripts import check_telegram

    monkeypatch.delenv("TG_LEAD_BOT_TOKEN", raising=False)
    monkeypatch.setattr(check_telegram, "BASE_DIR", tmp_path)
    (tmp_path / ".env").write_text("# комментарий\nTG_LEAD_BOT_TOKEN=123:abc\nBOT_API_TOKEN='secret'\n", encoding="utf-8")

    assert check_telegram.env_value("TG_LEAD_BOT_TOKEN") == "123:abc"
    assert check_telegram.env_value("BOT_API_TOKEN") == "secret"   # кавычки снимаются
    assert check_telegram.env_value("НЕТ_ТАКОГО") == ""


def test_legacy_bot_token_is_reported(monkeypatch, capsys):
    """Бот теперь один: оставшийся TG_BOT_TOKEN в .env — след старой настройки, диагностика о нём скажет."""
    import sys

    from scripts import check_telegram

    env = {
        "TG_LEAD_BOT_TOKEN": "123:abc",
        "TG_BOT_TOKEN": "456:def",
        "BOT_API_TOKEN": "secret",
        "API_URL": "http://127.0.0.1:9",
    }
    monkeypatch.setattr(check_telegram, "env_value", lambda name: env.get(name, ""))
    monkeypatch.setattr(check_telegram, "telegram", lambda *a, **k: {"ok": False, "description": "Unauthorized"})
    monkeypatch.setattr(check_telegram, "api_call", lambda *a, **k: (0, {}))
    monkeypatch.setattr(sys, "argv", ["check_telegram.py"])

    assert check_telegram.main() == 1  # 1 — из-за непроверенного токена, а не из-за старой переменной
    out = capsys.readouterr().out
    assert "TG_BOT_TOKEN больше не используется" in out
