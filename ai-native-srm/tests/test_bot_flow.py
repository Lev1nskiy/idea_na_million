"""Тесты диалога бота: меню, профиль, обращения, чат с ИИ, вызов менеджера.

Логика бота (`channels/bot_flow.py`) не знает ни про Telegram, ни про HTTP — сервис
подменяется фейком, поэтому весь диалог проверяется без сети и без токенов.
Здесь же — проверки mock-CRM (`channels/mock_crm.py`) и формата уведомлений менеджеру.
"""
from __future__ import annotations

import pytest

from channels import bot_ui as ui
from channels.bot_flow import BotError, BotSession, Event
from channels.manager_feed import manager_notification_text
from channels.mock_crm import MockCrm


# --------------------------------------------------------------------------- фейковый сервис
class FakeService:
    """CRM в памяти для тестов: запоминает вызовы и умеет «ломаться»."""

    def __init__(self, card: dict | None = None, appeals: list[dict] | None = None) -> None:
        self.card = card
        self.appeal_rows = appeals or []
        self.messages: dict[int, list[dict]] = {row["id"]: list(row.pop("messages_list", [])) for row in self.appeal_rows}
        self.saved: list[dict] = []
        self.chats: list[tuple[str, int | None]] = []
        self.manager_calls: list[int] = []
        self.closed: list[int] = []
        self.error = ""

    def _guard(self) -> None:
        if self.error:
            raise BotError(self.error)

    def profile(self):
        self._guard()
        return self.card

    def save_profile(self, answers):
        self._guard()
        self.saved.append(dict(answers))
        changed = [key for key, value in answers.items() if not self.card or self.card.get(key) != value]
        self.card = {"id": 1, "status": "new", "created_at": "2026-10-16T10:00:00", "appeals_total": len(self.appeal_rows),
                     "appeals_open": 0, **(self.card or {}), **answers}
        return {"ok": True, "created": False, "changed": changed, "client": self.card}

    def appeals(self, open_only: bool = False):
        self._guard()
        return [row for row in self.appeal_rows if not open_only or row["status"] in ("ACTIVE", "HOLD", "HANDOFF")]

    def appeal(self, conversation_id: int):
        self._guard()
        row = next(r for r in self.appeal_rows if r["id"] == conversation_id)
        return dict(row), list(self.messages.get(conversation_id, []))

    def chat(self, text: str, conversation_id: int | None = None):
        self._guard()
        self.chats.append((text, conversation_id))
        conv_id = conversation_id or 77
        self.messages.setdefault(conv_id, []).append({"role": "ai", "text": f"ответ на «{text}»", "phase": "FINAL"})
        return {"conversation_id": conv_id, "created_client": self.card is None, "messages": self.messages[conv_id]}

    def call_manager(self, conversation_id: int, reason: str = "") -> str:
        self._guard()
        self.manager_calls.append(conversation_id)
        return f"Передал обращение #{conversation_id} менеджеру."

    def close_appeal(self, conversation_id: int) -> str:
        self._guard()
        self.closed.append(conversation_id)
        return f"Обращение #{conversation_id} закрыто."

    def catalog(self):
        self._guard()
        return [{"sku": "PX-M4200", "name": "МФУ Printex M4200", "price": 38900, "stock": 12, "lead_time_days": 1, "warranty_months": 12}]

    def faq(self):
        self._guard()
        return [("Доставка и сроки", "Отгрузка 1 рабочий день."), ("Гарантия", "12 месяцев.")]


CARD = {
    "id": 7,
    "name": "Дмитрий Соколов",
    "company": "ООО «Ромашка»",
    "phone": "+79991234567",
    "email": "zakupki@romashka.ru",
    "status": "new",
    "created_at": "2026-10-01T09:00:00",
    "appeals_total": 2,
    "appeals_open": 1,
}

APPEAL_ACTIVE = {
    "id": 12,
    "status": "ACTIVE",
    "status_title": "в работе, отвечает ИИ",
    "category": "цена",
    "channel": "telegram",
    "created_at": "2026-10-15T12:00:00",
    "updated_at": "2026-10-16T09:30:00",
    "last_text": "Скидка возможна: автономно до 15%",
    "last_role": "ai",
    "messages": 4,
    "waiting_manager": False,
    "messages_list": [
        {"role": "client", "text": "Нужно 10 МФУ", "phase": "FINAL"},
        {"role": "ai", "text": "Скидка возможна: автономно до 15%", "phase": "FINAL"},
    ],
}


def session(service: FakeService | None = None, **kwargs) -> BotSession:
    return BotSession(service or FakeService(card=dict(CARD)), telegram_id="555", display_name="Дмитрий", **kwargs)


def texts(outs) -> str:
    return "\n".join(out.text for out in outs)


def buttons(outs) -> list[str]:
    return [button.text for out in outs for row in out.screen.rows for button in row]


def press(bot: BotSession, data: str) -> list:
    return bot.handle(Event.callback(data))


def say(bot: BotSession, text: str) -> list:
    return bot.handle(Event.message(text))


# --------------------------------------------------------------------------- главный экран
def test_start_shows_menu_with_profile_and_widget():
    outs = say(session(), "/start")
    text = texts(outs)
    assert "AI-native CRM" in text and "Дмитрий Соколов" in text
    assert "обращений: 2 (1 в работе)" in text  # сводка из карточки CRM
    assert outs[0].screen.menu is True  # нижний виджет-меню
    labels = " ".join(buttons(outs))
    for expected in ("Профиль", "Обращения", "Новое обращение", "Чат с ИИ", "Позвать менеджера", "Помощь", "Каталог", "Настройки"):
        assert expected in labels


def test_start_without_card_offers_to_fill_profile():
    outs = say(session(FakeService(card=None)), "/start")
    assert "Профиль не заполнен" in texts(outs)


@pytest.mark.parametrize(
    ("label", "expected"),
    (
        ("👤 Профиль", "Профиль"),
        ("📁 Мои обращения", "Мои обращения"),
        ("🆕 Обращение", "Новое обращение"),
        ("🤖 Чат с ИИ", "Чат с ИИ"),
        ("💬 Менеджер", "Менеджер"),
        ("❓ Помощь", "Помощь"),
        ("🖥 Каталог и цены", "Каталог и цены"),
        ("⚙️ Настройки", "Настройки"),
    ),
)
def test_widget_labels_open_sections(label, expected):
    """Кнопки нижнего меню — обычный текст, поэтому проверяем и такой путь входа."""
    outs = say(session(), label)
    assert expected in texts(outs)


def test_callback_navigation_edits_message_in_place():
    """Нажатие кнопки не плодит сообщения: экран правится там же, где была кнопка."""
    assert press(session(), "nav:profile")[0].edit is True
    assert press(session(), "set:notify")[0].edit is True
    assert say(session(), "👤 Профиль")[0].edit is False  # текст — это новое сообщение


def test_unknown_command_and_unknown_callback_do_not_break_dialog():
    assert "Не понял" in texts(say(session(), "/чтотостранное"))
    assert "Не понял" in texts(press(session(), "нет:такого"))


def test_id_command_shows_telegram_id_for_manager():
    assert "555" in texts(say(session(), "/id"))


# --------------------------------------------------------------------------- профиль
def test_profile_screen_shows_contacts():
    text = texts(press(session(), "nav:profile"))
    assert "Дмитрий Соколов" in text and "+79991234567" in text and "ООО «Ромашка»" in text


def test_profile_fill_wizard_rejects_bad_input_and_saves_profile():
    service = FakeService(card=None)
    bot = session(service)
    assert "Заполнить сейчас" in texts(press(bot, "nav:profile"))

    assert "Как вас зовут" in texts(press(bot, "prof:fill"))
    assert "обязательный" in texts(say(bot, "-"))  # имя пропускать нельзя, шаг не сдвигается

    for step in ("Дмитрий Соколов", "ООО «Ромашка»", "+7 999 123-45-67", "zakupki@romashka.ru"):
        outs = say(bot, step)

    assert service.saved and service.saved[0]["name"] == "Дмитрий Соколов"
    assert service.saved[0]["phone"] == "+79991234567"  # телефон нормализуется как в анкете
    assert "Профиль сохранён" in texts(outs)
    assert bot.state == "MENU"


def test_profile_field_edit_keeps_other_fields_and_allows_clearing():
    service = FakeService(card=dict(CARD))
    bot = session(service)
    outs = press(bot, "prof:edit")
    assert "Телефон" in texts(outs)

    outs = press(bot, "prof:edit:company")
    assert "Правка профиля" in texts(outs)
    outs = say(bot, "-")  # «-» в правке поля очищает значение

    assert service.saved[0]["company"] == ""
    assert service.saved[0]["name"] == "Дмитрий Соколов"  # остальные поля остаются как были
    assert service.saved[0]["phone"] == "+79991234567"


# --------------------------------------------------------------------------- обращения
def test_appeals_list_shows_status_and_opens_appeal():
    service = FakeService(card=dict(CARD), appeals=[dict(APPEAL_ACTIVE)])
    bot = session(service)
    text = texts(press(bot, "nav:appeals"))

    assert "#12" in text and "в работе, отвечает ИИ" in text
    assert "Скидка возможна" in text

    outs = press(bot, "ap:open:12")
    assert "Обращение #12" in texts(outs) and "Нужно 10 МФУ" in texts(outs)
    assert bot.chat_id == 12


def test_appeals_empty_state_offers_new_appeal():
    text = texts(press(session(FakeService(card=dict(CARD))), "nav:appeals"))
    assert "Обращений пока нет" in text


def test_new_appeal_goes_through_topic_and_ai_answer():
    service = FakeService(card=dict(CARD))
    bot = session(service)
    outs = press(bot, "nav:new")
    assert "О чём обращение" in texts(outs) and "Спор или брак" in " ".join(buttons(outs))

    outs = press(bot, "ap:topic:claim")
    assert bot.state == "APPEAL_TEXT" and "Спор или брак" in texts(outs)

    outs = say(bot, "Принтер пришёл с трещиной, нужна замена")
    sent, conversation = service.chats[0]
    assert "Есть претензия" in sent  # тема уходит первой строкой: пайплайн сразу видит, о чём речь
    assert "трещиной" in sent and conversation is None  # новое обращение, а не продолжение старого
    assert "ответ на" in texts(outs)
    assert bot.chat_id == 77


def test_chat_continues_same_conversation():
    service = FakeService(card=dict(CARD))
    bot = session(service)
    press(bot, "nav:chat")
    say(bot, "Есть PX-M4200?")
    say(bot, "а сколько стоит?")
    assert [conv for _, conv in service.chats] == [None, 77]


def test_free_text_in_menu_is_question_to_ai():
    service = FakeService(card=dict(CARD))
    bot = session(service)
    outs = say(bot, "Когда доставка?")
    assert service.chats[0][0] == "Когда доставка?"
    assert "ответ на" in texts(outs)


def test_hold_answer_tells_client_manager_is_coming():
    """Пайплайн перевёл диалог в HOLD — клиент должен увидеть, что менеджер в курсе."""
    service = FakeService(card=dict(CARD))
    service.chat = lambda text, conversation_id=None: {  # type: ignore[method-assign]
        "conversation_id": 5,
        "created_client": False,
        "messages": [{"role": "ai", "text": "Передал обращение менеджеру", "phase": "HOLD"}],
    }
    text = texts(say(session(service), "принтер сломался"))
    assert "передано менеджеру" in text


def test_call_manager_from_appeal_and_without_appeals():
    service = FakeService(card=dict(CARD), appeals=[dict(APPEAL_ACTIVE)])
    bot = session(service)
    outs = press(bot, "ap:mgr:12")
    assert service.manager_calls == [12]
    assert "менеджеру" in texts(outs)

    empty = session(FakeService(card=dict(CARD)))
    assert "Открытых обращений нет" in texts(press(empty, "nav:manager"))


def test_close_appeal_resets_chat_context():
    service = FakeService(card=dict(CARD), appeals=[dict(APPEAL_ACTIVE)])
    bot = session(service)
    press(bot, "ap:open:12")
    outs = press(bot, "ap:close:12")
    assert service.closed == [12]
    assert bot.chat_id is None and bot.state == "MENU"
    assert "закрыто" in texts(outs)


def test_reply_button_asks_for_message_in_same_appeal():
    bot = session(FakeService(card=dict(CARD), appeals=[dict(APPEAL_ACTIVE)]))
    outs = press(bot, "ap:reply:12")
    assert bot.state == "CHAT" and bot.chat_id == 12
    assert "Обращение #12" in texts(outs)


# --------------------------------------------------------------------------- справка и настройки
def test_faq_sections_wrap_around():
    bot = session()
    first = texts(press(bot, "faq:0"))
    assert "Доставка и сроки" in first and "Отгрузка" in first
    assert "Гарантия" in texts(press(bot, "faq:9"))  # индекс по модулю — всегда валидный раздел


def test_catalog_uses_crm_reference_data():
    text = texts(press(session(), "nav:catalog"))
    assert "PX-M4200" in text and "38 900" in text


def test_settings_toggle_notifications_and_tech_details():
    bot = session()
    assert "включено ✅" in texts(press(bot, "nav:settings"))
    press(bot, "set:notify")
    assert bot.notify_manager_replies is False
    press(bot, "set:techie")
    assert bot.techie is True

    service = FakeService(card=dict(CARD))
    outs = say(session(service, techie=True), "Когда доставка?")
    assert "тех. детали" in texts(outs)


def test_cancel_returns_to_menu_from_wizard():
    bot = session(FakeService(card=None))
    press(bot, "prof:fill")
    outs = say(bot, "/cancel")
    assert bot.state == "MENU" and bot.wizard is None
    assert "AI-native CRM" in texts(outs)


def test_crm_error_is_shown_as_friendly_message():
    service = FakeService(card=dict(CARD))
    service.error = "CRM недоступна"
    outs = press(session(service), "nav:appeals")
    assert "CRM недоступна" in texts(outs)


# --------------------------------------------------------------------------- mock-CRM
def test_mock_crm_answers_from_catalog_and_policy():
    crm = MockCrm()
    item = crm.chat("Есть ли PX-M4200 в наличии?")["messages"][-1]["text"]
    assert "PX-M4200" in item and "38 900" in item

    discount = crm.chat("Нужно 10 МФУ PX-M4200, посчитайте со скидкой")["messages"][-1]["text"]
    assert "10 × 38 900" in discount and "итого 342 320" in discount  # 12% — тир политики для 389 000 ₽


def test_mock_crm_dispute_goes_to_hold_and_notifies_manager():
    crm = MockCrm()
    crm.save_profile({"name": "Дмитрий"})
    reply = crm.chat("Принтер пришёл с браком, нужна замена")["messages"][-1]["text"]
    assert "менеджеру" in reply and "в течение часа" in reply  # срок из Policy Matrix
    appeals = crm.appeals()
    assert appeals[0]["status"] == "HOLD" and appeals[0]["waiting_manager"] is True
    assert any("HOLD" in note for note in crm.take_manager_notes())


def test_mock_crm_without_fact_does_not_invent_numbers():
    crm = MockCrm()
    crm.save_profile({"name": "Дмитрий"})
    reply = crm.chat("Делаете ли вы гравировку логотипа на корпусе принтера?")["messages"][-1]["text"]
    assert "Не хочу называть цифры" in reply
    assert crm.appeals()[0]["status"] == "HOLD"


def test_mock_crm_manager_reply_reaches_client_inbox():
    crm = MockCrm()
    crm.save_profile({"name": "Дмитрий"})
    conversation_id = crm.chat("Нужна замена")["conversation_id"]
    crm.manager_reply(conversation_id, "Замена согласована")

    items = crm.inbox()
    assert items[0]["role"] == "manager" and items[0]["text"] == "Замена согласована"
    assert crm.inbox() == []  # доставка «at-least-once»: второй раз не отдаём
    assert crm.appeals()[0]["status"] == "HANDOFF"


def test_mock_crm_close_marks_resolved():
    crm = MockCrm()
    conversation_id = crm.chat("Вопрос по доставке")["conversation_id"]
    assert "закрыто" in crm.close_appeal(conversation_id)
    assert crm.appeals()[0]["status"] == "RESOLVED"


def test_mock_crm_reuses_open_appeal_and_creates_new_after_close():
    crm = MockCrm()
    first = crm.chat("Вопрос по доставке")["conversation_id"]
    assert crm.chat("И ещё вопрос про гарантию")["conversation_id"] == first  # открытое обращение то же
    crm.close_appeal(first)
    assert crm.chat("Новый вопрос")["conversation_id"] != first


def test_bot_session_works_with_mock_crm_end_to_end():
    """Сквозной прогон: меню → профиль → обращение → ИИ → менеджер → закрытие."""
    crm = MockCrm(display_name="Демо Клиент")
    bot = BotSession(crm, telegram_id="mock-1", display_name="Демо Клиент")

    say(bot, "/start")
    press(bot, "prof:fill")
    for step in ("Дмитрий Соколов", "ООО «Ромашка»", "+7 999 123-45-67", "-"):
        say(bot, step)
    assert crm.profile()["name"] == "Дмитрий Соколов"

    press(bot, "nav:new")
    outs = press(bot, "ap:topic:delivery")
    assert "Доставка" in texts(outs)
    outs = say(bot, "Когда приедет заказ?")
    assert "1 рабочий день" in texts(outs)

    appeal_id = bot.chat_id
    assert appeal_id is not None
    outs = press(bot, f"ap:mgr:{appeal_id}")
    assert "менеджеру" in texts(outs)

    crm.manager_reply(appeal_id, "Курьер будет завтра")
    assert crm.inbox()[0]["text"] == "Курьер будет завтра"

    outs = press(bot, f"ap:close:{appeal_id}")
    assert "закрыто" in texts(outs)
    assert crm.appeals()[0]["status"] == "RESOLVED"


# --------------------------------------------------------------------------- уведомления менеджеру
def test_manager_notification_text_includes_escalation_details():
    note = {"type": "OVERRIDE_DUE", "client_name": "ООО Тест", "waited_min": 12, "reminder_level": 2,
            "conversation_id": 5, "text": "Срок SLA просрочен, нужно решение"}
    text = manager_notification_text(note, "http://localhost:3000")
    assert "ТРЕБУЕТСЯ РЕШЕНИЕ" in text and "ООО Тест" in text and "ждёт 12 мин" in text
    assert "напоминание 2" in text and "SLA просрочен" in text
    assert "http://localhost:3000 (диалог #5)" in text


def test_manager_notification_knows_new_types_and_default():
    assert "МЕНЕДЖЕРА" in manager_notification_text({"type": "HANDOFF_REQUEST", "text": "Клиент зовёт"})
    assert "ЗАЯВКА ИЗ БОТА" in manager_notification_text({"type": "LEAD", "text": "Новая заявка"})
    assert "Уведомление" in manager_notification_text({"type": "СОВСЕМ_НОВЫЙ", "text": "?"})


def test_widget_menu_has_all_sections_and_no_duplicates():
    labels = [label for row in ui.widget_menu() for label in row]
    assert len(labels) == len(set(labels)) == 8
    assert all(label in {button for button in labels} for label in labels)
