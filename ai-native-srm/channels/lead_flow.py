"""Логика анкеты для Telegram-бота-заявок: вопросы, проверки, итоговая карточка.

Специально без aiogram: это чистая функция состояния, поэтому её проверяют обычными тестами
(`tests/test_lead_flow.py`), а бот (`channels/telegram_lead_bot.py`) остаётся тонкой обвязкой.

Анкета умышленно короткая — то, что нужно менеджеру для звонка: имя, компания, телефон,
e-mail и что клиенту нужно. Всё, кроме имени и одного контакта, можно пропустить командой
«-» (или «пропустить»): если человек не готов оставлять телефон, заявка всё равно доедет,
а менеджер ответит ему в Telegram.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.leads import Lead, LeadError, build_lead, normalize_phone

SKIP = {"-", "—", "нет", "пропустить", "skip", "не хочу"}
CANCEL = {"отмена", "cancel", "/cancel", "стоп", "/stop"}


@dataclass(frozen=True)
class Question:
    """Один шаг анкеты: ключ в анкете, текст вопроса, подсказка и обязательность."""

    key: str
    text: str
    hint: str = ""
    required: bool = False


QUESTIONS: tuple[Question, ...] = (
    Question("name", "Как вас зовут?", "Например: Дмитрий Соколов", required=True),
    Question("company", "Как называется ваша компания?", "Если частное лицо — напишите «-»"),
    Question("phone", "Телефон для связи?", "Например: +7 999 123-45-67. Не готовы оставить — «-»"),
    Question("email", "E-mail для документов?", "Например: zakaz@company.ru. Можно «-»"),
    Question("request", "Что нужно? Коротко опишите задачу.", "Например: 10 принтеров LP-220, нужна скидка"),
)

GREETING = (
    "Здравствуйте! Я бот компании — приму заявку за минуту: спрошу имя, компанию, контакты и "
    "что вам нужно, и передам менеджеру.\n\n"
    "Данные попадут в CRM компании и будут использованы только для связи по заявке. "
    "Прервать в любой момент — команда /cancel."
)
DONE = (
    "Готово, заявка №{client_id} передана менеджеру{who}.\n"
    "Мы свяжемся с вами{phone} в рабочее время. Спасибо!"
)
SKIP_NOTE = "Хорошо, пропущу этот вопрос."
RESTART = "Заполним заново — начнём с начала."
BYE = "Анкета отменена. Если понадобится — напишите /start."


@dataclass
class LeadSession:
    """Состояние диалога: индекс текущего вопроса и собранные ответы."""

    index: int = 0
    answers: dict[str, str] = field(default_factory=dict)
    telegram_id: str | None = None
    telegram_username: str | None = None

    @property
    def finished(self) -> bool:
        return self.index >= len(QUESTIONS)

    @property
    def question(self) -> Question:
        return QUESTIONS[min(self.index, len(QUESTIONS) - 1)]

    def progress(self) -> str:
        """«Вопрос 2 из 5» — клиенту видно, что анкета короткая."""
        return f"Вопрос {min(self.index + 1, len(QUESTIONS))} из {len(QUESTIONS)}"

    def reset(self) -> None:
        self.index = 0
        self.answers.clear()


def prompt(session: LeadSession) -> str:
    """Текст очередного вопроса с подсказкой и прогрессом."""
    q = session.question
    lines = [f"{session.progress()}. {q.text}"]
    if q.hint:
        lines.append(f"({q.hint})")
    if not q.required:
        lines.append("Можно пропустить: «-»")
    return "\n".join(lines)


def is_cancel(text: str) -> bool:
    return text.strip().lower() in CANCEL


def accept(session: LeadSession, text: str) -> str | None:
    """Принимает ответ на текущий вопрос.

    Возвращает None, если ответ принят, или текст ошибки, который нужно показать клиенту
    (шаг при этом не сдвигается — человек просто повторит ввод).
    """
    value = text.strip()
    if not value:
        return "Пустое сообщение — напишите ответ или «-», чтобы пропустить."
    if value.lower() in SKIP:
        if session.question.required:
            return "Этот вопрос обязательный — без него менеджер не сможет перезвонить."
        session.answers[session.question.key] = ""
        session.index += 1
        return None
    if session.question.key == "phone":
        normalized = normalize_phone(value)
        if not normalized or len(normalized) < 12:
            return "Телефон не распознан. Пример: +7 999 123-45-67 или «-», чтобы пропустить."
        value = normalized
    session.answers[session.question.key] = value
    session.index += 1
    return None


def as_lead(session: LeadSession, *, request_min: bool = True) -> Lead:
    """Собирает анкету в Lead и валидирует её теми же правилами, что и сервер."""
    answers = session.answers
    request_text = answers.get("request", "") or ""
    if request_min and request_text and len(request_text.strip()) < 5:
        raise LeadError("Опишите задачу чуть подробнее — хотя бы несколько слов.")
    return build_lead(
        name=answers.get("name", ""),
        company=answers.get("company") or None,
        phone=answers.get("phone") or None,
        email=answers.get("email") or None,
        request=request_text or None,
        telegram_id=session.telegram_id,
        telegram_username=session.telegram_username,
        source="telegram_lead_bot",
        # контакт может быть только Telegram: человек пишет нам прямо в боте
        require_contacts=True,
    )


def summary(session: LeadSession) -> str:
    """Карточка перед отправкой: клиент видит, что именно уйдёт менеджеру."""
    rows = [
        ("Имя", session.answers.get("name")),
        ("Компания", session.answers.get("company")),
        ("Телефон", session.answers.get("phone")),
        ("E-mail", session.answers.get("email")),
        ("Задача", session.answers.get("request")),
    ]
    body = "\n".join(f"• {label}: {value or '—'}" for label, value in rows)
    return f"Проверьте, всё верно?\n{body}\n\nОтправляем — «да», заполнить заново — «заново»."


def confirm_done(client_id: int, lead: Lead) -> str:
    """Текст после успешной отправки: номер карточки и что будет дальше."""
    who = f" ({lead.company})" if lead.company else ""
    when = f" по телефону {lead.phone}" if lead.phone else " здесь, в Telegram,"
    return DONE.format(client_id=client_id, who=who, phone=when)
