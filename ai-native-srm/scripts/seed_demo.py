"""Демо-данные: клиенты в БД, seed политики, проверка справочников (Шаг 5).

    python scripts/seed_demo.py                 # клиенты + политика (идемпотентно)
    python scripts/seed_demo.py --reset         # сначала очистить runtime-таблицы

Идемпотентность: повторный запуск не плодит строк и не сбрасывает политику (создаёт
версию 1 только если БД пуста). Заказы/серийники живут в data/clients_history.json —
это справочник репозитория, он не дублируется в БД.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import SQLModel, select  # noqa: E402

from app import db as dbm  # noqa: E402
from app import models as m  # noqa: E402
from core.config import DATA_DIR  # noqa: E402
from core.policy import seed_policy_if_empty  # noqa: E402

DEMO_MANAGERS = ("Анна (продажи)", "Игорь (сервис)", "Ольга (старший менеджер)")


# Учётные записи сотрудников сброс демо-данных не трогает: иначе кнопка «Сбросить демо-данные»
# выкидывала бы администратора на экран входа прямо во время демонстрации.
AUTH_TABLES = {"users", "user_sessions"}


def seed(reset: bool = False) -> dict[str, int | bool]:
    """Заводит клиентов из справочника и политику. Возвращает отчёт для лога/тестов."""
    if reset:
        engine = dbm.get_engine()
        tables = [t for t in SQLModel.metadata.sorted_tables if t.name not in AUTH_TABLES]
        SQLModel.metadata.drop_all(engine, tables=tables)
    dbm.init_db()
    created_policy = seed_policy_if_empty()

    data = json.loads((DATA_DIR / "clients_history.json").read_text(encoding="utf-8"))
    added = 0
    with dbm.session_scope() as s:
        for entry in data["clients"]:
            row = s.get(m.Client, entry["client_id"])
            if row is None:
                row = m.Client(id=entry["client_id"])
                s.add(row)
                added += 1
            row.name = entry["name"]
            row.phone = entry.get("phone")
            row.email = entry.get("email")
            row.company = entry.get("company")
            row.status = entry.get("status", "new")
            row.telegram_id = entry.get("telegram_id")
        total = len(s.exec(select(m.Client)).all())
    return {"clients_added": added, "clients_total": total, "policy_seeded": created_policy}


def main() -> None:
    parser = argparse.ArgumentParser(description="Демо-данные для AI-native CRM")
    parser.add_argument("--reset", action="store_true", help="очистить runtime-таблицы перед загрузкой")
    args = parser.parse_args()

    report = seed(reset=args.reset)
    print(f"клиентов добавлено: {report['clients_added']}, всего в БД: {report['clients_total']}")
    print(f"политика: {'создана версия 1' if report['policy_seeded'] else 'уже есть, не трогаем'}")
    print("менеджеры демо-стенда: " + ", ".join(DEMO_MANAGERS))


if __name__ == "__main__":
    main()
