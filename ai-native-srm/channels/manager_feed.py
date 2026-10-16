"""Уведомления менеджеру в Telegram: один формат для всех типов событий.

Менеджер получает в мессенджер то же, что видит в ленте дашборда: спор, попытку инъекции,
«нужно решение», напоминание, заявку из бота и запрос менеджера от клиента. Формат один,
поэтому подписи типов и сборка текста живут здесь, а не в адаптере транспорта.

Модуль чистый (никаких обращений к окружению) — проверяется тестами без aiogram и сети.
"""
from __future__ import annotations

from typing import Any

#: Тип уведомления CRM → заголовок сообщения в Telegram.
TYPE_TITLES: dict[str, str] = {
    "DISPUTE": "⚖️ СПОР — нужен менеджер",
    "SECURITY": "🛡️ БЕЗОПАСНОСТЬ — попытка манипуляции",
    "OVERRIDE_DUE": "⏰ ТРЕБУЕТСЯ РЕШЕНИЕ — ИИ не уверен",
    "REMINDER": "🔔 НАПОМИНАНИЕ",
    "LEAD": "🆕 ЗАЯВКА ИЗ БОТА",
    "HANDOFF_REQUEST": "💬 КЛИЕНТ ЗОВЁТ МЕНЕДЖЕРА",
}
DEFAULT_TITLE = "🔔 Уведомление"


def manager_notification_text(note: dict[str, Any], ui_url: str = "") -> str:
    """Текст уведомления менеджеру: тип, клиент, сколько ждёт, напоминание, ссылка на дашборд.

    Повторы до takeover приходят снова: цикл HOLD помечает строку непрочитанной, когда
    поднимает reminder_level, поэтому в Telegram видно эскалацию, а не одно сообщение.
    """
    head = [TYPE_TITLES.get(note.get("type", ""), DEFAULT_TITLE)]
    if client := note.get("client_name"):
        head.append(str(client))
    waited = note.get("waited_min")
    if waited is not None:
        head.append(f"ждёт {waited} мин")
    if level := note.get("reminder_level"):
        head.append(f"напоминание {level}")
    body = str(note.get("text", ""))
    lines = [" · ".join(head), body]
    if "просроч" in body.lower():
        lines.append("⚠️ Срок SLA просрочен")
    if ui_url:
        lines.append(f"→ {ui_url} (диалог #{note.get('conversation_id')})")
    return "\n".join(line for line in lines if line)
