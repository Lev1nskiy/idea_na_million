"""Email/IMAP-канал — заглушка (роадмап п. 0 №11: IMAP официально в Future).

Решение по объёму MVP: каналов два — web-чат (обязателен для жюри) и Telegram.
Почта добавляется тем же способом, что и Telegram: адаптер принимает письмо,
шлёт текст в `POST /api/messages`, забирает ответы из `/api/outbox?channel=email`
и отвечает письмом. Ядро при этом не меняется — именно поэтому здесь нет кода:
писать «наполовину работающий» IMAP-клиент хуже, чем честно показать границу MVP.

Что нужно для реализации (после хакатона):
1. `IMAP_HOST/IMAP_USER/IMAP_PASSWORD` + опрос INBOX раз в N секунд;
2. сопоставление адреса отправителя с `Client.email` (как `telegram_id` у бота);
3. отправка через SMTP (`SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD`);
4. канал-строка `email` уже поддержана: `Channel = Literal["web", "telegram", "email"]`
   в core/schemas.py и `Outbox.channel` в app/models.py.
"""
from __future__ import annotations

from typing import NoReturn

NOT_IMPLEMENTED_MESSAGE = (
    "IMAP-канал не реализован в MVP: по плану (roadmap п. 0 №11) он в Future. "
    "Используйте web-чат (ui/) или Telegram (channels/telegram_bot.py)."
)


class ImapChannel:
    """Заглушка канала: интерфейс совпадает с нужным, но вызовы явно сообщают о статусе."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)


def run() -> NoReturn:
    """Точка входа для `python -m channels.email_imap` — честно сообщает, что канал в Future."""
    raise NotImplementedError(NOT_IMPLEMENTED_MESSAGE)


if __name__ == "__main__":  # pragma: no cover
    run()
