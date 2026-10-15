"""Привязка Telegram-аккаунта к клиенту (Шаг 6, подготовка демо-стенда).

    python scripts/bind_telegram.py --list
    python scripts/bind_telegram.py --client 4 --telegram-id 123456789
    python scripts/bind_telegram.py --client 4 --unbind

Зачем: адаптер Telegram (channels/telegram_bot.py) ищет клиента по `telegram_id`
(эндпоинт `GET /api/clients/by-telegram/{id}`). В справочнике `clients_history.json`
это поле пустое, потому что настоящий id появляется только у живого аккаунта —
узнайте свой у бота @userinfobot и привяжите здесь.

Скрипт меняет только одну колонку `clients.telegram_id` — данные клиентов,
заказы и история не затрагиваются.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import col, select  # noqa: E402

from app import db as dbm  # noqa: E402
from app import models as m  # noqa: E402


def bind(client_id: int, telegram_id: str | None) -> str:
    """Ставит (или снимает) привязку. Возвращает человекочитаемый результат."""
    dbm.init_db()
    with dbm.session_scope() as s:
        row = s.get(m.Client, client_id)
        if row is None:
            raise ValueError(f"Клиент #{client_id} не найден — выполните scripts/seed_demo.py")
        current = row.telegram_id
        row.telegram_id = telegram_id
        name = row.name
    if telegram_id is None:
        return f"Клиент #{client_id} ({name}): привязка снята (было {current or '—'})"
    return f"Клиент #{client_id} ({name}): Telegram {telegram_id or '—'} привязан"


def bindings() -> list[dict[str, str | int | None]]:
    """Текущие привязки: кто из клиентов уже виден боту."""
    dbm.init_db()
    with dbm.session_scope() as s:
        rows = list(s.exec(select(m.Client).order_by(col(m.Client.id))).all())
    return [{"client_id": r.id, "name": r.name, "telegram_id": r.telegram_id} for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Привязка Telegram к клиенту CRM")
    parser.add_argument("--client", type=int, help="id клиента (1–6 в демо-наборе)")
    parser.add_argument("--telegram-id", help="числовой id аккаунта Telegram (см. @userinfobot)")
    parser.add_argument("--unbind", action="store_true", help="снять привязку")
    parser.add_argument("--list", action="store_true", help="показать текущие привязки")
    args = parser.parse_args()

    if args.list:
        for row in bindings():
            print(f"  #{row['client_id']:>2} {row['name']:<20} telegram_id: {row['telegram_id'] or '—'}")
        return
    if args.client is None or (args.telegram_id is None and not args.unbind):
        parser.error("укажите --client и --telegram-id (или --unbind / --list)")
    try:
        print(bind(args.client, None if args.unbind else args.telegram_id))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
