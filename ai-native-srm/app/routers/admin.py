"""Админские эндпоинты переноса данных (дополнение к таблице эндпоинтов шага 5).

Все пути под /api/admin и защищены заголовком `X-Admin-Token` (ADMIN_TOKEN из .env).
Если ADMIN_TOKEN не задан — эндпоинты отвечают 503: наружу ПДн по умолчанию не уходят (Ст. 4).

Эндпоинты:
    GET  /api/admin/export?mask=true        — выгрузка состояния БД (по умолчанию маскированная)
    POST /api/admin/import                  — загрузка JSON-дампа (multipart, dry_run/mode/policy)
    GET  /api/admin/backup                  — бинарный снимок SQLite (консистентный, на живой БД)
    PUT  /api/admin/reference-data/{name}   — замена справочника из data/ с атомарной записью

Внимание: восстановление БД из снимка здесь НЕ реализовано сознательно — оно подменяет
файл и требует остановленного сервиса, поэтому делается скриптом scripts/db_backup.py --in.
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from core import rag, tools
from core.config import DATA_DIR, get_settings
from core.portability import ImportMode, export_state, import_state

router = APIRouter(prefix="/api/admin", tags=["admin"])

MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 МБ: больше для SQLite-демо не нужно

# Разрешённые к замене справочники: имя → (файл, ключ в JSON или None для текстовых)
_REFERENCE: dict[str, tuple[str, str | None]] = {
    "catalog": ("catalog.json", "items"),
    "clients_history": ("clients_history.json", "clients"),
    "quote_templates": ("quote_templates.json", "templates"),
    "knowledge_base": ("knowledge_base.md", None),
}


def require_admin(x_admin_token: Annotated[str | None, Header()] = None) -> None:
    """Простая защита админ-путей: общий токен из .env (в демо-стенде этого достаточно)."""
    token = get_settings().admin_token
    if not token:
        raise HTTPException(status_code=503, detail="админ-API выключен: не задан ADMIN_TOKEN")
    if x_admin_token != token:
        raise HTTPException(status_code=401, detail="неверный X-Admin-Token")


AdminOnly = Depends(require_admin)


def _db_backup_file() -> Path:
    """Консистентный снимок SQLite во временный файл (на живой БД)."""
    from scripts.db_backup import backup, db_path  # импорт внутри: скрипт нужен только здесь

    if not db_path().exists():
        raise HTTPException(status_code=404, detail="файла БД ещё нет")
    return backup(Path(tempfile.gettempdir()) / f"crm-{uuid.uuid4().hex}.sqlite3")


@router.get("/export", dependencies=[AdminOnly])
def export_dump(mask: bool = Query(default=True, description="маскировать ПДн (Ст. 4)")) -> JSONResponse:
    """Выгружает состояние БД одним JSON. mask=false — только для закрытого контура."""
    dump = export_state(mask=mask)
    filename = f"crm-state-{dump['exported_at'][:10]}{'-masked' if mask else ''}.json"
    return JSONResponse(
        content=dump,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import", dependencies=[AdminOnly])
async def import_dump(
    file: UploadFile,
    mode: ImportMode = Query(default="merge"),
    policy: bool = Query(default=False),
    dry_run: bool = Query(default=False),
) -> dict[str, Any]:
    """Загружает JSON-дамп. Импорт атомарный: ошибка → откат, БД не меняется."""
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


@router.get("/backup", dependencies=[AdminOnly])
def sqlite_backup() -> FileResponse:
    """Отдаёт бинарный снимок SQLite — его можно восстановить через scripts/db_backup.py --in."""
    path = _db_backup_file()
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


@router.get("/reference-data", dependencies=[AdminOnly])
def list_reference_data() -> dict[str, list[str]]:
    """Какие справочники можно заменить и что сейчас лежит в data/."""
    return {"allowed": sorted(_REFERENCE), "files": sorted(p.name for p in DATA_DIR.glob("*"))}


@router.put("/reference-data/{name}", dependencies=[AdminOnly])
async def replace_reference_data(name: str, file: UploadFile) -> dict[str, Any]:
    """Заменяет справочник (каталог, база клиентов, шаблоны КП, база знаний).

    Запись атомарная (tmp + os.replace): параллельные чтения не увидят полузаписанный файл.
    После записи сбрасываются кэши tools/rag — новые данные видны следующему запросу.
    """
    if name not in _REFERENCE:
        raise HTTPException(status_code=404, detail=f"неизвестный справочник: {name}")
    filename, key = _REFERENCE[name]
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="пустой файл")

    target = DATA_DIR / filename
    if key is None:  # текстовый справочник (knowledge_base.md)
        if len(raw) < 100:
            raise HTTPException(status_code=400, detail="слишком короткий текст базы знаний")
        records = None
    else:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"не JSON: {exc.msg}") from exc
        records = payload.get(key) if isinstance(payload, dict) else None
        if not isinstance(records, list) or not records:
            raise HTTPException(status_code=400, detail=f"ожидался непустой список в ключе {key!r}")

    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, target)  # атомарная замена
    tools.reload_data()  # сброс кэша каталога/клиентов/шаблонов
    if key is None:
        rag.reload()  # сброс кэша базы знаний
    return {"file": filename, "records": len(records) if records is not None else None}
