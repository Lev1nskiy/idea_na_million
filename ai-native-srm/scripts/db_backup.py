"""Консистентный бэкап и восстановление SQLite-базы (шаг 6: деплой).

    python scripts/db_backup.py --out backups/crm.db --rotate 7 --stamp   # ежедневный cron
    python scripts/db_backup.py --in backups/crm.db      # восстановление (сервис остановлен!)

Почему не `cp crm.db`: при активных записях копия может оказаться битой — журнал
(-journal/-wal) в файл не попадает. Здесь используется `sqlite3.Connection.backup()`:
снимок делается на ЖИВОЙ базе, без остановки сервиса и без риска полузаписи.
Восстановление подменяет файл атомарно (`os.replace`) и сохраняет текущую базу
в `*.pre-restore`. Для Postgres этот скрипт не нужен — там pg_dump/pg_restore.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_settings  # noqa: E402

_REQUIRED_TABLES = {"conversations", "messages", "policy_versions"}


def db_path() -> Path:
    """Путь к файлу SQLite из DB_URL (например, sqlite:////app/crm.db)."""
    url = get_settings().db_url
    if not url.startswith("sqlite"):
        raise SystemExit(f"db_backup работает только с SQLite, DB_URL={url!r}")
    return Path(url.removeprefix("sqlite:///"))


def backup(dest: Path, source: Path | None = None) -> Path:
    """Консистентный снимок живой БД в файл dest.

    `source` — явный путь к базе (нужен сервису: он снимает копию той БД, к которой
    фактически подключён движок, а не той, что записана в DB_URL).
    """
    source_path = source or db_path()
    if not source_path.exists():
        raise SystemExit(f"файла БД нет: {source_path}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)  # блокирует запись ровно на время копирования страниц
    finally:
        source.close()
        target.close()
    return dest


def restore(src: Path) -> Path:
    """Заменяет рабочую БД снимком src. Запускать ТОЛЬКО при остановленном сервисе."""
    dst = db_path()
    if not src.exists():
        raise SystemExit(f"снимка нет: {src}")
    check = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in check.execute("select name from sqlite_master where type='table'")}
    finally:
        check.close()
    if _REQUIRED_TABLES - tables:
        raise SystemExit(f"это не похоже на БД CRM; нет таблиц: {sorted(_REQUIRED_TABLES - tables)}")
    if dst.exists():
        shutil.copy2(dst, dst.with_name(dst.name + ".pre-restore"))
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)  # атомарная подмена
    for suffix in ("-journal", "-wal", "-shm"):  # журналы старой БД больше не нужны
        Path(str(dst) + suffix).unlink(missing_ok=True)
    return dst


def stamped(path: Path, now: datetime | None = None) -> Path:
    """Добавляет к имени снимка метку времени: crm.db → crm-20261015-031500.db.

    Нужно для регулярных бэкапов по cron: без метки каждый запуск перезаписывает один и тот же
    файл, и «хранить 7 снимков» превращается в «хранить один». С меткой файлы копятся, а
    `--rotate 7` удаляет самые старые.
    """
    moment = now or datetime.now()
    return path.with_name(f"{path.stem}-{moment:%Y%m%d-%H%M%S}{path.suffix}")


def rotate(pattern: str, keep: int) -> list[Path]:
    """Оставляет `keep` самых свежих снимков, остальные удаляет (ротация бэкапов)."""
    folder = Path(pattern).parent
    name = Path(pattern).name
    prefix = name.split("*")[0]
    files = sorted(
        (p for p in folder.glob(name) if p.name.startswith(prefix)),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        old.unlink()
    return files[:keep]


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкап/восстановление SQLite (ai-native-crm)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", help="файл снимка для бэкапа")
    group.add_argument("--in", dest="restore_from", help="снимок для восстановления")
    parser.add_argument("--rotate", type=int, default=0, help="сколько снимков хранить (0 — не чистить)")
    parser.add_argument("--stamp", action="store_true", help="добавить к имени снимка метку времени (cron)")
    args = parser.parse_args()

    if args.out:
        target = stamped(Path(args.out)) if args.stamp else Path(args.out)
        path = backup(target)
        size_kb = path.stat().st_size / 1024
        print(f"бэкап: {path} ({size_kb:.0f} КБ)")
        if args.rotate:
            kept = rotate(str(Path(args.out).with_name("*" + Path(args.out).suffix)), args.rotate)
            print(f"оставлено снимков: {len(kept)}")
    else:
        path = restore(Path(args.restore_from))
        print(f"восстановлено: {path} (предыдущая база сохранена как {path.name}.pre-restore)")


if __name__ == "__main__":
    main()
