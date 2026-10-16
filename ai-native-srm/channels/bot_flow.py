"""Логика диалога бота: состояния, переходы и реакции на кнопки — без aiogram и сети.

Здесь живёт «мозг» бота: что показать на «👤 Профиль», как провести анкету, что сделать
после «💬 Позвать менеджера». Все обращения к CRM спрятаны за протоколом `BotService`,
поэтому одна и та же логика работает:
  • в Telegram — через `channels/telegram_lead_bot.BotApi` (HTTP на `/api/bot/*`);
  • офлайн, без токенов и сети — через `channels/mock_crm.MockCrm` (см. `mock_telegram.py`).
Это и есть «система mock»: mock-LLM считает ответы ИИ, mock-CRM хранит карточки и
обращения, mock-транспорт рисует экраны в терминале.

Состояния: MENU → (PROFILE_FILL | PROFILE_FIELD | APPEAL_TEXT | CHAT) → MENU.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Protocol

from channels import bot_ui as ui
from channels import lead_flow as lf

class BotError(RuntimeError):
    """Понятная клиенту ошибка CRM (текст можно показывать в чате).

    `status` — HTTP-код ответа CRM, если он был: по нему транспорт отличает «клиента ещё
    нет в CRM» (404 — это не сбой) от настоящей поломки.
    """

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- контракт с CRM
class BotService(Protocol):
    """Что боту нужно от CRM. Реализуется HTTP-клиентом (`BotApi`) и mock-CRM."""

    def profile(self) -> dict[str, Any] | None: ...

    def save_profile(self, answers: dict[str, str]) -> dict[str, Any]: ...

    def appeals(self, open_only: bool = False) -> list[dict[str, Any]]: ...

    def appeal(self, conversation_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]: ...

    def chat(self, text: str, conversation_id: int | None = None) -> dict[str, Any]: ...

    def call_manager(self, conversation_id: int, reason: str = "") -> str: ...

    def close_appeal(self, conversation_id: int) -> str: ...

    def catalog(self) -> list[dict[str, Any]]: ...

    def faq(self) -> list[tuple[str, str]]: ...


# --------------------------------------------------------------------------- события и ответы
@dataclass(frozen=True)
class Event:
    """Входящее событие бота: текст, команда или нажатие inline-кнопки."""

    kind: str  # text | command | callback
    text: str = ""

    @classmethod
    def message(cls, text: str) -> "Event":
        stripped = text.strip()
        kind = "command" if stripped.startswith("/") else "text"
        return cls(kind=kind, text=stripped)

    @classmethod
    def callback(cls, data: str) -> "Event":
        return cls(kind="callback", text=data.strip())


@dataclass(frozen=True)
class Out:
    """Что транспорт должен показать клиенту: экран и способ его отрисовки."""

    screen: ui.Screen
    edit: bool = False  # заменить текст текущего сообщения (навигация по кнопкам)
    notify: str = ""  # всплывающая подсказка (answerCallbackQuery)

    @property
    def text(self) -> str:
        return self.screen.text


# --------------------------------------------------------------------------- диалог
@dataclass
class BotSession:
    """Состояние диалога одного клиента.

    Живёт в памяти процесса: при рестарте бота клиент просто нажмёт /start. Санкционированные
    данные (профиль, обращения) хранит CRM — бот не дублирует их у себя.
    """

    service: BotService
    telegram_id: str = ""
    display_name: str = ""
    username: str = ""
    state: str = "MENU"
    wizard: lf.LeadSession | None = None
    wizard_mode: str = ""  # fill (вся анкета) | field (одно поле профиля)
    topic: str = ""
    chat_id: int | None = None  # обращение, в котором идёт чат с ИИ
    notify_manager_replies: bool = True
    techie: bool = False
    pending_text: str = ""  # текст, который не смогли отправить (предлагаем «повторить»)
    log: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    def _card(self) -> dict[str, Any] | None:
        try:
            return self.service.profile()
        except BotError:
            return None

    def _appeals(self) -> list[dict[str, Any]]:
        return self.service.appeals()

    def _remember(self, out: Out) -> Out:
        self.log.append(ui.plain(out.text)[:300])
        return out

    # ------------------------------------------------------------------ точки входа
    def handle(self, event: Event) -> list[Out]:
        """Обрабатывает событие и возвращает, что показать (экранов может быть несколько)."""
        try:
            if event.kind == "callback":
                return self._emit(self._callback(event.text))
            if event.kind == "command":
                return self._emit(self._command(event.text))
            return self._emit(self._text(event.text))
        except BotError as exc:
            return [self._remember(Out(ui.error_screen(f"{exc}")))]

    def _emit(self, outs: list[Out]) -> list[Out]:
        return [self._remember(out) for out in outs]

    # ------------------------------------------------------------------ команды
    def _command(self, command: str) -> list[Out]:
        name = command.split()[0].split("@")[0].lower()
        if name in ("/start", "/menu"):
            self._reset()
            return [Out(self._main())]
        if name == "/cancel":
            self._reset()
            return [Out(self._main(), notify="Отменил, вернулся в меню")]
        if name == "/help":
            return [Out(ui.help_screen(self._card() is not None))]
        if name == "/profile":
            return self._nav("profile")
        if name == "/appeals":
            return self._nav("appeals")
        if name == "/chat":
            return self._nav("chat")
        if name == "/manager":
            return self._nav("manager")
        if name == "/id":
            who = f"{self.display_name} (@{self.username})" if self.username else (self.display_name or "без имени")
            return [
                Out(
                    ui.Screen(
                        f"🆔 Ваш Telegram ID: <code>{self.telegram_id}</code>\n{who}\n\n"
                        "Назовите его менеджеру — по нему CRM найдёт вашу карточку.",
                        rows=(),
                    )
                )
            ]
        return [Out(ui.unknown_screen())]

    # ------------------------------------------------------------------ нажатия кнопок
    def _callback(self, data: str) -> list[Out]:
        if data == "noop":
            return [Out(self._main(), notify="Менеджер уже уведомлён — ждите ответа здесь")]
        if data.startswith("nav:"):
            return self._nav(data.split(":", 1)[1])
        if data == "prof:fill":
            return self._begin_wizard(lf.PROFILE_QUESTIONS, mode="fill")
        if data == "prof:edit":
            return [Out(self._edit_menu())]
        if data.startswith("prof:edit:"):
            return self._begin_field_edit(data.split(":", 2)[2])
        if data.startswith("ap:topic:"):
            return self._begin_appeal(data.split(":", 2)[2])
        if data.startswith("ap:open:"):
            return self._open_appeal(int(data.rsplit(":", 1)[1]))
        if data.startswith("ap:reply:"):
            return self._reply_to(int(data.rsplit(":", 1)[1]))
        if data.startswith("ap:close:"):
            return self._close_appeal(int(data.rsplit(":", 1)[1]))
        if data.startswith("ap:mgr:"):
            return self._call_manager(int(data.rsplit(":", 1)[1]))
        if data.startswith("ap:ask:"):
            sku = data.split(":", 2)[2]
            return self._send_to_ai(f"Расскажите про {sku}: цена, наличие и сроки поставки.", None)
        if data.startswith("faq:"):
            return [Out(self._faq(int(data.split(":", 1)[1])))]
        if data == "set:notify":
            self.notify_manager_replies = not self.notify_manager_replies
            return [Out(ui.settings_screen(self.notify_manager_replies, self.techie), edit=True)]
        if data == "set:techie":
            self.techie = not self.techie
            return [Out(ui.settings_screen(self.notify_manager_replies, self.techie), edit=True)]
        return [Out(ui.unknown_screen())]

    # ------------------------------------------------------------------ текст
    def _text(self, text: str) -> list[Out]:
        if text in _WIDGET_NAV:  # нажали кнопку нижнего меню
            return self._nav(_WIDGET_NAV[text])
        if self.state == "PROFILE_FILL" or self.state == "PROFILE_FIELD":
            return self._wizard_step(text)
        if self.state == "APPEAL_TEXT":
            _, hint = ui.TOPIC_BY_SLUG.get(self.topic, ("", ""))
            body = f"{hint}\n{text}".strip() if hint else text
            self.state = "MENU"
            return self._send_to_ai(body, None)
        if self.state == "CHAT":
            return self._send_to_ai(text, self.chat_id)
        return self._send_to_ai(text, None)  # в меню любой текст — это вопрос ИИ

    # ------------------------------------------------------------------ навигация
    def _main(self) -> ui.Screen:
        card = self._card()
        if card is None:
            return ui.main_screen(None, 0, 0)
        return ui.main_screen(card.get("name"), card.get("appeals_open", 0), card.get("appeals_total", 0))

    def _nav(self, target: str) -> list[Out]:
        self.state = "MENU"
        card = self._card()
        if target == "menu":
            return [Out(self._main())]
        if target == "profile":
            return [Out(ui.profile_screen(card) if card else ui.profile_missing_screen())]
        if target == "appeals":
            return [Out(ui.appeals_screen(self._appeals()))]
        if target == "new":
            self.state = "APPEAL_TEXT"
            self.topic = ""
            return [Out(ui.topics_screen())]
        if target == "chat":
            self.state = "CHAT"
            self.chat_id = None
            return [
                Out(
                    ui.Screen(
                        "🤖 <b>Чат с ИИ</b>\n\n"
                        "Спрашивайте про товары, цены, наличие, доставку, гарантию или ремонт — "
                        "отвечу сразу. Если вопрос спорный, честно скажу и позову менеджера.\n\n"
                        "Напишите сообщение.",
                        rows=((ui.btn("💬 Позвать менеджера", "nav:manager"), ui.btn("📁 Обращения", "nav:appeals")),),
                    )
                )
            ]
        if target == "manager":
            return [Out(ui.manager_screen(self._appeals()))]
        if target == "help":
            return [Out(ui.help_screen(card is not None))]
        if target == "catalog":
            return [Out(ui.catalog_screen(self.service.catalog()))]
        if target == "settings":
            return [Out(ui.settings_screen(self.notify_manager_replies, self.techie))]
        return [Out(ui.unknown_screen())]

    # ------------------------------------------------------------------ профиль
    def _edit_menu(self) -> ui.Screen:
        card = self._card() or {}
        rows = tuple(
            (ui.btn(f"✏️ {title}", f"prof:edit:{key}"),)
            for key, title in (("name", "Имя"), ("company", "Компания"), ("phone", "Телефон"), ("email", "E-mail"))
        )
        return ui.Screen(
            f"✏️ <b>Что изменить?</b>\n\nИмя: {card.get('name', '—')}\n"
            f"Компания: {card.get('company') or '—'}\nТелефон: {card.get('phone') or '—'}\n"
            f"E-mail: {card.get('email') or '—'}",
            rows=rows + ((ui.btn("🏠 В меню", "nav:menu"),),),
        )

    def _begin_wizard(self, questions: tuple[lf.Question, ...], mode: str) -> list[Out]:
        self.wizard = lf.LeadSession(questions=questions, telegram_id=self.telegram_id, telegram_username=self.username)
        self.wizard_mode = mode
        self.state = "PROFILE_FILL"
        return [Out(ui.question_screen(lf.prompt(self.wizard), self.wizard.progress()))]

    def _begin_field_edit(self, key: str) -> list[Out]:
        question = lf.QUESTION_BY_KEY.get(key)
        if question is None:
            return [Out(ui.unknown_screen())]
        # в режиме правки «-» означает «очистить поле»: подсказываем это прямо в вопросе
        self.wizard = lf.LeadSession(questions=(dataclasses.replace(question, hint="«-» — очистить поле"),))
        self.wizard_mode = "field"
        self.state = "PROFILE_FIELD"
        return [Out(ui.question_screen(lf.prompt(self.wizard), "Правка профиля"))]

    def _wizard_step(self, text: str) -> list[Out]:
        assert self.wizard is not None
        error = lf.accept(self.wizard, text)
        if error:
            return [Out(ui.question_screen(error + "\n\n" + lf.prompt(self.wizard), self.wizard.progress()))]
        if not self.wizard.finished:
            return [Out(ui.question_screen(lf.prompt(self.wizard), self.wizard.progress()))]
        answers = dict(self.wizard.answers)
        if self.wizard_mode == "field":
            card = self._card() or {}
            answers = {
                "name": card.get("name", ""),
                "company": card.get("company", ""),
                "phone": card.get("phone", ""),
                "email": card.get("email", ""),
                **answers,
            }
        if not answers.get("name"):
            answers["name"] = self.display_name or self._card_name() or "Клиент Telegram"
        self._reset()
        report = self.service.save_profile(answers)
        card = report.get("client", {})
        return [Out(ui.profile_saved_screen(card, report.get("changed", [])), notify="Профиль сохранён")]

    def _card_name(self) -> str:
        card = self._card() or {}
        return str(card.get("name") or "")

    # ------------------------------------------------------------------ обращения
    def _begin_appeal(self, slug: str) -> list[Out]:
        if slug not in ui.TOPIC_BY_SLUG:
            return [Out(ui.unknown_screen())]
        self.topic = slug
        self.state = "APPEAL_TEXT"
        label, _ = ui.TOPIC_BY_SLUG[slug]
        return [Out(ui.appeal_text_screen(label))]

    def _send_to_ai(self, text: str, conversation_id: int | None) -> list[Out]:
        """Отправляет сообщение в пайплайн и показывает ответ ИИ."""
        result = self.service.chat(text, conversation_id)
        self.chat_id = result.get("conversation_id") or self.chat_id
        self.state = "CHAT"
        messages = result.get("messages") or []
        answer = [m for m in messages if m.get("role") in ("ai", "manager")] or messages
        outs = [Out(ui.chat_screen(answer, techie=self.techie, conversation_id=self.chat_id))]
        if result.get("created_client"):
            outs.append(Out(self._main(), notify="Завёл карточку в CRM — заполните профиль, чтобы менеджер мог позвонить"))
        return outs

    def _open_appeal(self, conversation_id: int) -> list[Out]:
        appeal, messages = self.service.appeal(conversation_id)
        self.chat_id = conversation_id
        self.state = "MENU"
        return [Out(ui.appeal_screen(appeal, messages, techie=self.techie))]

    def _reply_to(self, conversation_id: int) -> list[Out]:
        self.chat_id = conversation_id
        self.state = "CHAT"
        return [
            Out(
                ui.Screen(
                    f"✍️ <b>Обращение #{conversation_id}</b>\n\nНапишите сообщение — оно уйдёт в CRM.",
                    rows=((ui.btn("📄 Открыть переписку", f"ap:open:{conversation_id}"),),),
                ),
                notify="Пишите — я передам в CRM",
            )
        ]

    def _close_appeal(self, conversation_id: int) -> list[Out]:
        text = self.service.close_appeal(conversation_id)
        if self.chat_id == conversation_id:
            self.chat_id = None
        self.state = "MENU"
        return [Out(ui.appeal_closed_screen(text), notify="Обращение закрыто")]

    def _call_manager(self, conversation_id: int) -> list[Out]:
        text = self.service.call_manager(conversation_id)
        self.state = "MENU"
        return [Out(ui.manager_called_screen(text), notify="Менеджер уведомлён")]

    # ------------------------------------------------------------------ справка
    def _faq(self, index: int) -> ui.Screen:
        sections = self.service.faq()
        if not sections:
            return ui.error_screen("База знаний пуста — спросите ИИ или позовите менеджера.")
        return ui.faq_screen(sections, index % len(sections))

    # ------------------------------------------------------------------ служебное
    def _reset(self) -> None:
        self.state = "MENU"
        self.wizard = None
        self.wizard_mode = ""
        self.topic = ""
        self.pending_text = ""


#: Подписи нижнего меню → раздел. Дублируется смысл inline-кнопок: клиент может нажать
#: и «настоящую» кнопку Telegram, и просто отправить текст с тем же словом.
_WIDGET_NAV: dict[str, str] = {
    ui.BTN_NEW: "new",
    ui.BTN_APPEALS: "appeals",
    ui.BTN_PROFILE: "profile",
    ui.BTN_CHAT: "chat",
    ui.BTN_MANAGER: "manager",
    ui.BTN_HELP: "help",
    ui.BTN_CATALOG: "catalog",
    ui.BTN_SETTINGS: "settings",
}
