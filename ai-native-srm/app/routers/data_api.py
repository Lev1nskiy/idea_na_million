"""Ручки страницы «Данные» на сайте: выгрузка, загрузка и снимок БД.

Пути под /api/site/data, доступ — только администратору (та же проверка, что у политики:
`core.auth.admin_user`). Логика переноса общая с админ-API (`app/transfer.py`), поэтому
кнопки в сайте и CLI-контуры работают по одним правилам.

    GET  /api/site/data/summary   — что лежит в БД (для отчёта на странице)
    GET  /api/site/data/export    — дамп состояния JSON (mask=true по умолчанию)
    GET  /api/site/data/backup    — бинарный снимок SQLite
    POST /api/site/data/import    — загрузка JSON-дампа (multipart; dry_run, mode, policy)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app import transfer
from core import auth
from core.portability import ImportMode

router = APIRouter(prefix="/api/site/data", tags=["site-data"], dependencies=[Depends(auth.current_user)])
admin_only = [Depends(auth.admin_user)]


@router.get("/summary", dependencies=admin_only)
def data_summary() -> dict[str, Any]:
    """Сводка для страницы «Данные»: сколько записей в таблицах и какого размера БД."""
    return transfer.summary()


@router.get("/export", dependencies=admin_only)
def data_export(mask: bool = Query(default=True, description="маскировать ПДн (Ст. 4)")) -> JSONResponse:
    """Выгрузка состояния. mask=false отдаёт контакты как есть — только для закрытого контура."""
    return transfer.build_export(mask)


@router.get("/backup", dependencies=admin_only)
def data_backup() -> FileResponse:
    """Снимок файла БД: страховка перед импортом и способ унести данные в другой контур."""
    return transfer.build_backup()


@router.post("/import", dependencies=admin_only)
async def data_import(
    file: UploadFile,
    mode: ImportMode = Query(default="merge", description="merge — дополнить, replace — заменить"),
    policy: bool = Query(default=False, description="переносить ли версии политики"),
    dry_run: bool = Query(default=False, description="только проверить, ничего не записывать"),
) -> dict[str, Any]:
    """Загрузка дампа. Импорт атомарный: при ошибке БД остаётся нетронутой."""
    return await transfer.run_import(file, mode=mode, policy=policy, dry_run=dry_run)
