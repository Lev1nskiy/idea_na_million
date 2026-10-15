"""Маскирование персональных данных в логах/events/инцидентах (Ст. 4).

Полный текст сообщений хранится только в таблице messages (закрытая БД).
Всё, что уходит в logging, events.payload и security_incidents, проходит mask_pii().
Ограничение прототипа: имена без отчества («меня зовут Анна») не распознаются.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

# +7 (999) 123-45-67 | 8 999 123 45 67 | 89991234567
_PHONE = re.compile(
    r"(?<!\w)(?:\+7|8|7)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)

# SN-ABC123456 | S/N: ABC123456
_SERIAL_PREFIX = re.compile(r"\b(?:SN[-:#]|S/N[\s:#-]*)\s?[A-Z0-9][A-Z0-9\-]{5,}")
# «серийный номер XYZ123456»
_SERIAL_CONTEXT = re.compile(
    r"(серийн\w*\s*(?:номер\w*|№|#)?\s*[:#]?\s*)([A-Za-z0-9][A-Za-z0-9\-]{5,})",
    re.IGNORECASE,
)

# 150 000 ₽ | 1 500 000 руб. | 90000 рублей | 50 тыс
_AMOUNT = re.compile(
    r"(?<![\w.])\d+(?:[\u00a0 ]\d{3})*(?:[.,]\d+)?\s?"
    r"(?:₽|руб(?:\.|л\w*)?|р\.|тыс(?:\.|яч\w*)?|RUB)(?!\w)",
    re.IGNORECASE,
)

# Иванов Иван Иванович | Иван Иванович | Иванов И.И.
_NAME_PATRONYMIC = re.compile(
    r"\b(?:[А-ЯЁ][а-яё]+\s+){1,2}[А-ЯЁ][а-яё]*(?:вич|вна|ична|ьич)\b"
)
_NAME_INITIALS = re.compile(r"\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.")


def mask_pii(text: str) -> str:
    """Заменяет email, телефоны, серийники, суммы и ФИО на плейсхолдеры."""
    if not text:
        return text
    out = _EMAIL.sub("[EMAIL]", text)
    out = _PHONE.sub("[PHONE]", out)
    out = _SERIAL_PREFIX.sub("[SERIAL]", out)
    out = _SERIAL_CONTEXT.sub(lambda m: m.group(1) + "[SERIAL]", out)
    out = _AMOUNT.sub("[AMOUNT]", out)
    out = _NAME_PATRONYMIC.sub("[NAME]", out)
    out = _NAME_INITIALS.sub("[NAME]", out)
    return out


def mask_payload(obj: Any) -> Any:
    """Рекурсивно маскирует все строки в dict/list (для events.payload)."""
    if isinstance(obj, str):
        return mask_pii(obj)
    if isinstance(obj, dict):
        return {k: mask_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_payload(v) for v in obj]
    return obj


def text_hash(text: str) -> str:
    """Стабильный короткий хэш текста (нормализация: trim + lower + схлопывание пробелов)."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class PiiLogFilter(logging.Filter):
    """logging.Filter, маскирующий ПДн в сообщении записи."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_pii(record.getMessage())
        record.args = None
        return True


_installed = False


def install_pii_log_filter() -> None:
    """Глобально маскирует ПДн во всех логах процесса (через record factory)."""
    global _installed
    if _installed:
        return
    old_factory = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = old_factory(*args, **kwargs)
        record.msg = mask_pii(record.getMessage())
        record.args = None
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
