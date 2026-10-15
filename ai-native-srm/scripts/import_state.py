"""Импорт JSON-дампа в БД.

    python scripts/import_state.py --in dumps/state.json --dry-run     # проверить, ничего не менять
    python scripts/import_state.py --in dumps/state.json               # merge по id (идемпотентно)
    python scripts/import_state.py --in dumps/state.json --mode replace --policy

--mode replace — предварительно очистить таблицы дампа (данные вне дампа не трогаются);
--policy      — последняя политика из дампа станет НОВОЙ версией (история не перезаписывается).

Импорт атомарный: при любой ошибке в БД не меняется ничего — смотрите отчёт.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402
from core.portability import import_state_from_file  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт состояния CRM из JSON")
    parser.add_argument("--in", dest="source", required=True, help="файл дампа (JSON)")
    parser.add_argument("--mode", choices=("merge", "replace"), default="merge")
    parser.add_argument("--policy", action="store_true", help="применить политику из дампа новой версией")
    parser.add_argument("--dry-run", action="store_true", help="проверка без изменений в БД")
    args = parser.parse_args()

    init_db()
    report = import_state_from_file(
        args.source, mode=args.mode, policy=args.policy, dry_run=args.dry_run
    )

    mode = "проверка (dry-run)" if report.dry_run else ("зафиксировано" if report.committed else "ОТКАЧЕНО")
    print(f"импорт: {args.source} — {mode}")
    for name, count in report.imported.items():
        print(f"  {name}: +{count}")
    for name, count in report.skipped.items():
        print(f"  {name}: пропущено {count}")
    if report.policy_version is not None:
        print(f"  политика → версия {report.policy_version}")
    if report.errors:
        print(f"  ошибок: {len(report.errors)}")
        for error in report.errors[:10]:
            print(f"    {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
