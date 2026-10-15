"""Тесты бота-анкеты: чистая логика опроса (без Telegram) + сборка Dispatcher."""
from __future__ import annotations

import asyncio
import json

import pytest

from channels import lead_flow as lf
from channels import telegram_lead_bot as tlb
from core.leads import LeadError


class FakeApi:
    """Подменяет HTTP: запоминает отправленные анкеты и умеет «падать»."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    def submit(self, lead) -> dict:
        if self.fail:
            raise RuntimeError("API недоступен (тест)")
        self.sent.append({"name": lead.name, "phone": lead.phone, "company": lead.company, "request": lead.request})
        return {"ok": True, "client_id": 1007, "created": True, "changed": False}

    def health(self) -> dict:
        return {"status": "ok", "provider": "mock"}


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


# --- Диалог: команды и подтверждение ----------------------------------------------------------

def test_start_greets_and_shows_first_question():
    session = lf.LeadSession()
    answer, state, send = tlb.handle_command("/start", session, "NONE")
    assert state == "FILL" and not send
    assert "Здравствуйте" in answer and "Как вас зовут" in answer


def test_cancel_stops_the_survey():
    session = lf.LeadSession()
    _fill(session, ["Имя"])
    answer, state, send = tlb.handle_command("отмена", session, "FILL")
    assert (answer, state, send) == (lf.BYE, "NONE", False)
    assert session.index == 0 and not session.answers


def test_confirm_yes_triggers_sending_only_with_agreement():
    session = lf.LeadSession()
    answer, state, send = tlb.handle_command("да", session, "CONFIRM")
    assert send and state == "SENDING" and answer == ""

    answer, state, send = tlb.handle_command("наверное", session, "CONFIRM")
    assert not send and state == "CONFIRM" and "«да»" in answer


def test_restart_from_confirm_clears_answers():
    session = lf.LeadSession()
    _fill(session, ["Имя", "ООО", "+79990001122", "-", "задача"])
    answer, state, send = tlb.handle_command("заново", session, "CONFIRM")
    assert state == "FILL" and not send and session.index == 0
    assert "заново" in answer.lower()


def test_handle_text_moves_to_summary_at_the_end():
    session = lf.LeadSession(telegram_id="5")
    _fill(session, ["Имя", "ООО", "+79990001122", "-", "задача"])
    text, error = tlb.handle_text("что-то", session)
    assert error is None and "Проверьте" in text


def test_handle_text_reports_skip_note():
    session = lf.LeadSession()
    _fill(session, ["Имя"])
    text, error = tlb.handle_text("-", session)
    assert error is None and "пропущу" in text and "3 из 5" in text


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


def test_manager_message_has_card_number_and_no_pii_duplication():
    session = lf.LeadSession(telegram_id="5", telegram_username="dm")
    _fill(session, ["Дмитрий", "ООО Ромашка", "+79990001122", "-", "10 принтеров"])
    text = tlb.manager_message({"client_id": 1007, "created": True}, lf.as_lead(session), "http://localhost:3000")
    assert "#1007" in text and "ООО Ромашка" in text and "10 принтеров" in text
    assert "+79990001122" not in text  # контакт берём из карточки, не размножаем ПДн в чатах
    assert "http://localhost:3000/dashboard" in text


def test_submit_sends_lead_to_api_and_notifies_manager():
    """Полный проход через фейковый aiogram: анкета уходит на сервер, менеджер получает копию."""
    pytest.importorskip("aiogram")
    from aiogram import Bot

    sent: list[tuple[int, str]] = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    config = tlb.LeadBotConfig(
        token="123456:AAaaAAaaAAaaAAaaAAaaAAaaAAaaAAaaAAa",
        api_url="http://localhost:8000",
        api_token="secret",
        manager_chat_id="777",
    )
    api = FakeApi()
    bot = Bot(token=config.token)
    try:
        router = tlb.build_dispatcher(bot, api, config)
        assert len(router.message.handlers) == 2  # команды и текстовый обработчик

        async def scenario() -> None:
            session = lf.LeadSession(telegram_id="5")
            _fill(session, ["Дмитрий", "ООО Ромашка", "+79990001122", "-", "10 принтеров"])
            lead = tlb.build_lead_from(session)
            report = api.submit(lead)
            await FakeBot().send_message(777, tlb.manager_message(report, lead, config.ui_url))

        asyncio.run(scenario())
    finally:
        asyncio.run(bot.session.close())

    assert api.sent[0]["name"] == "Дмитрий"
    assert api.sent[0]["phone"] == "+79990001122"
    assert sent and sent[0][0] == 777 and "#1007" in sent[0][1]


def test_submit_error_keeps_contact_and_asks_to_retry():
    """Сервер недоступен: клиент не теряет анкету, а получает просьбу повторить."""
    session = lf.LeadSession(telegram_id="5")
    _fill(session, ["Дмитрий", "-", "+79990001122", "-", "заявка"])
    lead = tlb.build_lead_from(session)

    with pytest.raises(RuntimeError):
        FakeApi(fail=True).submit(lead)

    assert lead.phone == "+79990001122"  # данные остались в сессии — отправка повторится


def test_check_reports_missing_settings_without_network(monkeypatch, capsys):
    monkeypatch.delenv("TG_LEAD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_API_TOKEN", raising=False)
    monkeypatch.setenv("API_URL", "http://127.0.0.1:9")  # заведомо закрытый порт
    code = asyncio.run(tlb.check())
    out = capsys.readouterr().out
    assert code == 1
    assert "НЕ задан" in out and "НЕ отвечает" in out


def test_lead_api_posts_json_with_bot_token(monkeypatch):
    """Проверяем сам HTTP-вызов: заголовок токена и тело без None-полей."""
    captured: dict = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"client_id": 1007}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        captured["body"] = json.loads(request.data.decode())
        return FakeResponse()

    monkeypatch.setattr(tlb.urllib.request, "urlopen", fake_urlopen)
    api = tlb.LeadApi(tlb.LeadBotConfig(token="t", api_url="http://api.local", api_token="secret"))
    session = lf.LeadSession(telegram_id="5", telegram_username="dm")
    _fill(session, ["Дмитрий", "-", "+79990001122", "-", "заявка"])
    report = api.submit(lf.as_lead(session))

    assert report["client_id"] == 1007
    assert captured["url"] == "http://api.local/api/leads"
    assert captured["headers"]["x-bot-token"] == "secret"
    assert captured["body"]["source"] == "telegram_lead_bot"
    assert captured["body"]["telegram_id"] == "5"
