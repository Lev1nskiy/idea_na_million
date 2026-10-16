"""Проверка Telegram-бота через настоящий aiogram-диспетчер — без сети, токенов и модели.

Тесты в `test_bot_flow.py` проверяют логику диалога напрямую (BotSession), а здесь апдейт
идёт тем же путём, что и в Telegram: Update → Dispatcher → хендлер → экран → «отправка».
Поэтому тест ловит именно то, что видит клиент: появилось ли виджет-меню, правятся ли
экраны inline-кнопками, доезжает ли ответ менеджера из CRM.
"""

import asyncio
import time
from datetime import datetime
from typing import Any

import pytest

pytest.importorskip("aiogram", reason="aiogram нужен только для проверки живого пути апдейта")

from aiogram.types import Chat, Message, Update  # noqa: E402

from channels import bot_ui as ui  # noqa: E402
from channels import telegram_lead_bot as tlb  # noqa: E402
from channels.mock_crm import MockCrm  # noqa: E402

CHAT_ID = 555


# --------------------------------------------------------------------------- заглушки Telegram
def method_by_name(name: str) -> Any:
    """`send_message` → класс метода SendMessage (чтобы заглушка отвечала как настоящий Bot)."""
    import aiogram.methods as methods

    return getattr(methods, "".join(part.title() for part in name.split("_")), None)


class RecordingBot:
    """Вместо сети записывает вызовы Telegram API и отдаёт правдоподобные ответы."""

    id = 777
    username = "test_bot"

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        """aiogram зовёт bot(method), а наш код доставки — bot.send_message(...): поддерживаем оба пути."""
        method = method_by_name(name)
        if method is None or name.startswith("_"):
            raise AttributeError(name)

        async def call(**kwargs: Any) -> Any:
            return await self(method(**kwargs))

        return call

    async def __call__(self, method: Any, request_timeout: float | None = None) -> Any:
        self.calls.append(method)
        if type(method).__name__ in {"AnswerCallbackQuery", "SetMyCommands", "SetMyDescription", "SetMyShortDescription"}:
            return True
        return Message(
            message_id=100 + len(self.calls),
            date=datetime.now(),
            chat=Chat(id=getattr(method, "chat_id", CHAT_ID), type="private"),
            text=getattr(method, "text", ""),
        )

    def screen_texts(self, calls: list[Any]) -> list[str]:
        """Тексты экранов, которые клиент реально увидел (отправка или правка сообщения)."""
        return [call.text for call in calls if type(call).__name__ in {"SendMessage", "EditMessageText"}]

    def sent(self) -> list[Any]:
        return [call for call in self.calls if type(call).__name__ == "SendMessage"]

    def edited(self) -> list[Any]:
        return [call for call in self.calls if type(call).__name__ == "EditMessageText"]

    def texts(self) -> list[str]:
        return [call.text for call in self.calls if type(call).__name__ in {"SendMessage", "EditMessageText"}]

    def clear(self) -> None:
        self.calls.clear()


class FakeApi(MockCrm):
    """Тот же интерфейс, что у BotApi, но без HTTP: профиль, обращения и ответы — в памяти."""

    def __init__(self, config: Any, telegram_id: str = "", display_name: str = "", username: str = "") -> None:
        super().__init__()
        self.config = config
        self.telegram_id = telegram_id
        self.display_name = display_name
        self.username = username


def message_update(text: str, update_id: int = 1, chat_id: int = CHAT_ID) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "Дмитрий", "language_code": "ru"},
            "text": text,
        },
    }


def callback_update(data: str, update_id: int = 2, chat_id: int = CHAT_ID) -> dict[str, Any]:
    payload = message_update("нажатие", update_id, chat_id)
    message = payload.pop("message")
    payload["callback_query"] = {
        "id": str(update_id),
        "from": message["from"],
        "chat_instance": "test-chat",
        "data": data,
        "message": message,
    }
    return payload


class Harness:
    """Мини-стенд: диспетчер, поддельный Telegram и mock-CRM в одном месте.

    Диспетчер сам создаёт клиента CRM на каждый чат, поэтому фабрика запоминает
    созданные экземпляры — через них тест смотрит, что реально ушло в CRM.
    """

    def __init__(self) -> None:
        self.bot = RecordingBot()
        self.apis: dict[str, FakeApi] = {}
        self.config = tlb.LeadBotConfig(token="1:x", api_url="http://crm.test", api_token="secret")
        self.dispatcher = tlb.build_dispatcher(self.bot, self.config)

    @property
    def crm(self) -> FakeApi:
        """CRM того чата, с которым работает тест."""
        return self.apis[str(CHAT_ID)]

    async def feed(self, payload: dict[str, Any]) -> list[Any]:
        self.bot.clear()
        await self.dispatcher.feed_update(self.bot, Update.model_validate(payload, context={"bot": self.bot}))
        return self.bot.calls

    async def text(self, text: str, update_id: int = 1) -> list[Any]:
        return await self.feed(message_update(text, update_id))

    async def tap(self, data: str, update_id: int = 2) -> list[Any]:
        return await self.feed(callback_update(data, update_id))


@pytest.fixture()
def harness(monkeypatch: pytest.MonkeyPatch) -> Harness:
    """Подменяет HTTP-клиент CRM на память: сеть в тестах не нужна."""
    harness = Harness()

    def factory(config: Any, telegram_id: str = "", display_name: str = "", username: str = "") -> FakeApi:
        api = FakeApi(config, telegram_id, display_name, username)
        harness.apis[telegram_id] = api
        return api

    monkeypatch.setattr(tlb, "BotApi", factory)
    return harness


# --------------------------------------------------------------------------- меню и навигация
def test_start_shows_inline_menu_and_widget_keyboard(harness: Harness) -> None:
    """Виджет-меню — то, из-за чего клиент вообще видит разделы в Telegram."""
    calls = asyncio.run(harness.text("/start"))
    sent = [call for call in calls if type(call).__name__ == "SendMessage"]
    assert len(sent) == 2, "ожидались экран меню и подсказка про нижнюю клавиатуру"

    screen, widget = sent
    assert "AI-native CRM" in screen.text
    labels = [b.text for row in screen.reply_markup.inline_keyboard for b in row]
    assert any("Обращения" in label for label in labels), "на экране меню должны быть разделы"
    assert len(labels) >= 8, "все разделы меню должны быть доступны кнопками"

    assert type(widget.reply_markup).__name__ == "ReplyKeyboardMarkup"
    assert [[b.text for b in row] for row in widget.reply_markup.keyboard] == [list(row) for row in ui.widget_menu()]
    assert widget.reply_markup.resize_keyboard is True


def test_widget_button_opens_section(harness: Harness) -> None:
    """Кнопка нижнего меню приходит обычным сообщением и открывает раздел."""
    asyncio.run(harness.text("/start"))
    calls = asyncio.run(harness.text("👤 Профиль", update_id=3))
    assert "Профиль" in calls[0].text
    assert "Заполнить" in " ".join(b.text for row in calls[0].reply_markup.inline_keyboard for b in row)


def test_inline_navigation_edits_same_message(harness: Harness) -> None:
    """Inline-навигация правит сообщение, а не плодит новые — диалог остаётся чистым."""
    asyncio.run(harness.text("/start"))
    calls = asyncio.run(harness.tap("nav:catalog"))
    assert harness.bot.edited(), "экран должен правиться inline-кнопкой"
    assert not harness.bot.sent(), "новое сообщение на навигацию не отправляется"
    assert any("Каталог" in call.text for call in harness.bot.edited())  # noqa
    assert any(type(call).__name__ == "AnswerCallbackQuery" for call in calls), "нажатие нужно подтвердить"


def test_widget_is_sent_once_per_chat(harness: Harness) -> None:
    asyncio.run(harness.text("/start"))
    calls = asyncio.run(harness.text("/start", update_id=9))
    assert [type(call).__name__ for call in calls] == ["SendMessage"], "подсказка про виджет не должна повторяться"
    assert "AI-native CRM" in calls[0].text


# --------------------------------------------------------------------------- диалог целиком
def test_profile_wizard_through_chat(harness: Harness) -> None:
    """Анкета проходит целиком: вопрос → ответ → сохранение в CRM."""
    asyncio.run(harness.text("/start"))
    first = harness.bot.screen_texts(asyncio.run(harness.tap("prof:fill")))
    assert "Как вас зовут" in first[0]

    calls: list[Any] = []
    for index, answer in enumerate(["Дмитрий Соколов", "ООО «Ромашка»", "+7 999 000-11-22", "-"], start=3):
        calls = asyncio.run(harness.text(answer, update_id=index))
    assert "Профиль сохранён" in harness.bot.screen_texts(calls)[0]
    assert harness.crm.profile()["name"] == "Дмитрий Соколов"
    assert harness.crm.profile()["phone"].replace(" ", "") == "+79990001122", "телефон нормализуется CRM"


def test_free_text_gets_ai_answer(harness: Harness) -> None:
    """Свободный вопрос уходит в пайплайн CRM и возвращается ответом ИИ."""
    asyncio.run(harness.text("/start"))
    calls = asyncio.run(harness.text("Есть ли PX-M4200 в наличии?", update_id=4))
    assert "PX-M4200" in calls[0].text
    assert harness.crm.appeal_rows, "под вопросом должен появиться диалог в CRM"


def test_manager_reply_reaches_client_through_delivery_loop(harness: Harness) -> None:
    """Ответ менеджера из дашборда доезжает до клиента: так работает канал «менеджер → Telegram»."""
    asyncio.run(harness.text("/start"))
    asyncio.run(harness.text("Есть ли PX-M4200 в наличии?", update_id=4))
    conversation_id = harness.crm.appeal_rows[0]["id"]
    harness.crm.manager_reply(conversation_id, "Гарантия 12 месяцев, сервис на выезде.")

    async def one_cycle() -> None:
        task = asyncio.create_task(tlb.deliver_loop(harness.bot, harness.dispatcher, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

    harness.bot.clear()
    asyncio.run(one_cycle())
    texts = " ".join(harness.bot.texts())
    assert "Менеджер" in texts and "Гарантия 12 месяцев" in texts


def test_help_and_id_command(harness: Harness) -> None:
    calls = asyncio.run(harness.text("/help"))
    assert "Команды" in calls[0].text and "/manager" in calls[0].text
    calls = asyncio.run(harness.text("/id", update_id=5))
    assert str(CHAT_ID) in calls[0].text
