"""Заявки из внешних каналов → карточка клиента в CRM.

Сюда приходит анкета из Telegram-бота (`channels/telegram_lead_bot.py`): имя, компания,
телефон, e-mail, что нужно. Модуль нормализует и проверяет данные, находит уже известного
клиента (по telegram_id → телефону → почте), обновляет только пустые или изменившиеся поля
и заводит уведомление менеджеру. Персональные данные при этом не пишутся в логи: payload
события маскируется (`core.privacy`), а сам текст заявки в журнал не попадает.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from app import models as m
from core.learning import log_event

#: Телефон: допускаем +7/8, скобки, дефисы и пробелы — нормализуем к «+7XXXXXXXXXX».
_PHONE_CLEAN = re.compile(r"[^\d+]")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-zА-Яа-я]{2,}$")
_REQUEST_MIN = 5
_NAME_MIN = 2

DEFAULT_STATUS = "new"          # так же, как в модели Client
SOURCE_EVENT = "lead_received"  # тип события в журнале LEARNING
NOTIFY_TYPE = "LEAD"            # тип уведомления менеджеру


class LeadError(ValueError):
    """Анкета не проходит проверку: текст ошибки можно показывать клиенту в боте."""


@dataclass
class Lead:
    """Нормализованная заявка."""

    name: str
    company: str | None = None
    phone: str | None = None
    email: str | None = None
    request: str | None = None
    telegram_id: str | None = None
    telegram_username: str | None = None
    source: str = "telegram"

    def as_payload(self) -> dict[str, Any]:
        """Объект для журнала/уведомления — без контактов (ПДн не уходят в логи)."""
        return {
            "source": self.source,
            "company": self.company,
            "has_phone": bool(self.phone),
            "has_email": bool(self.email),
            "request_len": len(self.request or ""),
        }


def normalize_phone(raw: str | None) -> str | None:
    """«8 (999) 123-45-67» → «+79991234567». Пустое значение остаётся пустым."""
    if not raw:
        return None
    digits = _PHONE_CLEAN.sub("", raw).lstrip("+")
    digits = digits.lstrip("+")
    if len(digits) == 11 and digits[0] in {"7", "8"}:
        return f"+7{digits[1:]}"
    if len(digits) == 10:  # без кода страны: 9991234567
        return f"+7{digits}"
    return f"+{digits}" if digits else None


def build_lead(
    *,
    name: str = "",
    company: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    request: str | None = None,
    telegram_id: str | int | None = None,
    telegram_username: str | None = None,
    source: str = "telegram",
    require_contacts: bool = True,
) -> Lead:
    """Проверяет и нормализует анкету. Ошибки — LeadError с текстом для клиента.

    require_contacts=False разрешает заявку без телефона и почты (например, клиент
    согласился оставить только Telegram — тогда писать менеджеру будем в чат бота).
    """
    clean_name = " ".join((name or "").split())
    if len(clean_name) < _NAME_MIN:
        raise LeadError("Имя слишком короткое: нужно минимум 2 символа")
    clean_request = " ".join((request or "").split()) or None
    if require_contacts and not (phone or email or telegram_id):
        raise LeadError("Нужен хотя бы один контакт: телефон, e-mail или Telegram")
    clean_phone = normalize_phone(phone)
    clean_email = (email or "").strip().lower() or None
    if clean_email and not _EMAIL.match(clean_email):
        raise LeadError("E-mail выглядит некорректно — проверьте адрес")
    return Lead(
        name=clean_name,
        company=" ".join((company or "").split()) or None,
        phone=clean_phone,
        email=clean_email,
        request=clean_request,
        telegram_id=str(telegram_id) if telegram_id else None,
        telegram_username=(telegram_username or "").lstrip("@") or None,
        source=source,
    )


def _find_client(session: Session, lead: Lead) -> m.Client | None:
    """Ищем клиента по устойчивым признакам: Telegram → телефон → e-mail."""
    if lead.telegram_id:
        found = session.exec(select(m.Client).where(m.Client.telegram_id == lead.telegram_id)).first()
        if found:
            return found
    if lead.phone:
        found = session.exec(select(m.Client).where(m.Client.phone == lead.phone)).first()
        if found:
            return found
    if lead.email:
        found = session.exec(select(m.Client).where(m.Client.email == lead.email)).first()
        if found:
            return found
    return None


def _merge(client: m.Client, lead: Lead) -> tuple[list[str], str | None]:
    """Дозаполняет карточку, не затирая уже известные данные.

    Возвращает (изменённые поля, чужой Telegram-аккаунт). Привязку Telegram не перезаписываем:
    по ней клиентский чат-бот находит человека, и подмена аккаунта молча лишила бы его истории
    переписки. Про второй аккаунт сообщаем менеджеру — перепривязать можно осознанно.
    """
    changed: list[str] = []
    alt_telegram: str | None = None
    for field in ("company", "phone", "email"):
        value = getattr(lead, field)
        if value and getattr(client, field) != value:
            setattr(client, field, value)
            changed.append(field)
    if lead.telegram_id:
        if not client.telegram_id:
            client.telegram_id = lead.telegram_id
            changed.append("telegram_id")
        elif client.telegram_id != lead.telegram_id:
            alt_telegram = lead.telegram_id
    return changed, alt_telegram


def apply_lead(lead: Lead, session: Session | None = None) -> dict[str, Any]:
    """Заводит или обновляет клиента и уведомляет менеджера.

    Возвращает отчёт для бота и API: id клиента, что создано/обновлено, текст уведомления.
    """
    from app.db import session_scope  # локальный импорт: модуль используют и тесты с фикстурами

    if session is None:
        with session_scope() as own:
            return apply_lead(lead, own)

    client = _find_client(session, lead)
    created = client is None
    changed: list[str] = []
    alt_telegram: str | None = None
    if created:
        client = m.Client(
            name=lead.name,
            phone=lead.phone,
            email=lead.email,
            company=lead.company,
            status=DEFAULT_STATUS,
            telegram_id=lead.telegram_id,
        )
        session.add(client)
        session.flush()  # нужен id для уведомления и ответа
    else:
        assert client is not None
        if lead.name and client.name != lead.name:
            # имя из анкеты свежее: клиент мог назваться иначе, чем в старой карточке
            client.name, changed = lead.name, changed + ["name"]
        merged, alt_telegram = _merge(client, lead)
        changed += merged
        session.add(client)

    note_text = _notification_text(lead, client_id=client.id, created=created, alt_telegram=alt_telegram)
    session.add(m.Notification(type=NOTIFY_TYPE, conversation_id=None, text=note_text))
    log_event(
        session,
        SOURCE_EVENT,
        None,
        {
            "client_id": client.id,
            "created": created,
            "changed": changed,
            "alt_telegram": bool(alt_telegram),
            **lead.as_payload(),
        },
    )
    return {
        "client_id": client.id,
        "created": created,
        "changed": changed,
        "name": client.name,
        "company": client.company,
        "notification": note_text,
    }


def _notification_text(
    lead: Lead, *, client_id: int | None, created: bool, alt_telegram: str | None = None
) -> str:
    """Короткое уведомление менеджеру: без телефона и почты (они есть в карточке)."""
    who = lead.company or lead.name
    action = "Новая заявка" if created else "Заявка от известного клиента"
    need = f" — {lead.request[:120]}" if lead.request else ""
    other = " [пишет из другого аккаунта Telegram — привязка не менялась]" if alt_telegram else ""
    return f"{action} из Telegram: {who}{need} (карточка клиента #{client_id}){other}"
