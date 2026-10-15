"""Экспорт состояния БД в JSON-дамп.

    python scripts/export_state.py --out dumps/state.json
    python scripts/export_state.py --out dumps/state_masked.json --mask   # без ПДн (Ст. 4)

Дамп включает все runtime-таблицы БД. Справочники из data/ в него не попадают —
они переносятся вместе с репозиторием.
Для полного бэкапа «1:1» (с возможностью восстановления) используйте scripts/db_backup.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db  # noqa: E402
from core.portability import export_state_to_file  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Экспорт состояния CRM в JSON")
    parser.add_argument("--out", required=True, help="файл дампа (JSON)")
    parser.add_argument("--mask", action="store_true", help="маскировать ПДн (Ст. 4)")
    args = parser.parse_args()

    init_db()
    counts = export_state_to_file(args.out, mask=args.mask)
    total = sum(counts.values())
    print(f"дамп: {args.out} (строк: {total})")
    for name, count in counts.items():
        if count:
            print(f"  {name}: {count}")
    if not args.mask:
        print("ВНИМАНИЕ: дамп содержит ПДн (Ст. 4) — хранить только в закрытом контуре")


if __name__ == "__main__":
    main()
