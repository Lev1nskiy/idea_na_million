"""Общая логика переноса данных: ею пользуются админ-API и ручки сайта.

Выгрузка/загрузка состояния живёт в `core.portability` (там же правила совместимости версий),
здесь — транспорт и защита: размер файла, коды ошибок, имя файла для скачивания, сводка по БД.
Один код на два входа (`/api/admin/*` с X-Admin-Token и `/api/site/data/*` с сессией админа),
чтобы правила переноса не разъезжались между контурами.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import func, select

from app import models as m
from app.db import session_scope
from core.portability import ImportMode, export_state, import_state

MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 МБ: больше для SQLite-демо не нужно

#: Разрешённые к замене справочники: имя → (файл, ключ в JSON или None для текстовых)
REFERENCE: dict[str, tuple[str, str | None]] = {
    "catalog": ("catalog.json", "items"),
    "clients_history": ("clients_history.json", "clients"),
    "quote_templates": ("quote_templates.json", "templates"),
    "knowledge_base": ("knowledge_base.md", None),
}

#: Что показываем в сводке на странице «Данные»: подпись → модель.
SUMMARY_TABLES: dict[str, Any] = {
    "clients": m.Client,
    "conversations": m.Conversation,
    "messages": m.Message,
    "deals": m.Deal,
    "notifications": m.Notification,
    "events": m.Event,
    "users": m.User,
}


def db_file() -> Path:
    """Файл SQLite, к которому сервис действительно подключён.

    Берём его у движка, а не только из DB_URL: так сводка, снимок и данные на экране
    говорят об одной и той же базе (тесты и стенды настраивают движок программно).
    """
    from app.db import get_engine

    database = get_engine().url.database
    if not database:
        raise HTTPException(status_code=400, detail="перенос файла БД доступен только для SQLite-контура")
    return Path(database)


def db_backup_file() -> Path:
    """Консистентный снимок SQLite во временный файл (на живой БД)."""
    from scripts.db_backup import backup  # импорт внутри: скрипт нужен только здесь

    source = db_file()
    if not source.exists():
        raise HTTPException(status_code=404, detail="файла БД ещё нет")
    return backup(Path(tempfile.gettempdir()) / f"crm-{uuid.uuid4().hex}.sqlite3", source=source)


def build_export(mask: bool = True) -> JSONResponse:
    """Выгрузка состояния БД одним JSON. mask=false — только для закрытого контура."""
    dump = export_state(mask=mask)
    filename = f"crm-state-{dump['exported_at'][:10]}{'-masked' if mask else ''}.json"
    return JSONResponse(
        content=dump,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def run_import(
    file: UploadFile,
    *,
    mode: ImportMode = "merge",
    policy: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Загрузка JSON-дампа. Импорт атомарный: ошибка → откат, БД не меняется."""
    raw = await file.read()
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail=f"дамп больше {MAX_IMPORT_BYTES // 1024 // 1024} МБ")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"не JSON: {exc.msg}") from exc
    try:
        report = import_state(payload, mode=mode, policy=policy, dry_run=dry_run)
    except ValueError as exc:  # несовместимая версия схемы / маскированный дамп
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if report.errors:
        raise HTTPException(status_code=422, detail=report.model_dump())
    return report.model_dump()


def build_backup() -> FileResponse:
    """Скачивание файла БД: имя с датой, чтобы снимки не перезаписывали друг друга."""
    path = db_backup_file()
    from core.schemas import utcnow

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"crm-{utcnow():%Y-%m-%d-%H%M}.sqlite3",
    )


def summary() -> dict[str, Any]:
    """Сводка для страницы «Данные»: что лежит в БД и когда это выгружали."""
    from core.policy import get_policy_version

    counts: dict[str, int] = {}
    with session_scope() as session:
        for name, model in SUMMARY_TABLES.items():
            counts[name] = int(session.exec(select(func.count()).select_from(model)).one() or 0)

    path = db_file()
    return {
        "tables": counts,
        "policy_version": get_policy_version(),
        "db_size_bytes": path.stat().st_size if path.exists() else 0,
        "db_file": path.name,
    }
