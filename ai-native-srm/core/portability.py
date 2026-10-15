"""Экспорт/импорт состояния CRM (runtime-таблицы БД).

Зачем: перенос демо-данных между машинами, выгрузка для аудита заказчика,
восстановление после сбоя.

Что попадает в дамп: зеркало клиентов, диалоги, сообщения, сделки, решения, handoff,
уведомления, инциденты безопасности, версии политики, события, outbox.
Что НЕ попадает: справочники из `data/` (catalog.json, clients_history.json,
quote_templates.json, knowledge_base.md, lexicon.json, rules.md) — это файлы
репозитория, они переносятся вместе с кодом. Загрузка каталога/базы клиентов
заказчика — app/routers/admin.py, PUT /api/admin/reference-data/{name}.

Формат дампа — один JSON:
    {"schema_version": 1, "exported_at": "...", "masked": false,
     "counts": {...}, "tables": {"clients": [...], "conversations": [...]}}

Импорт атомарный: любая ошибка → rollback, БД остаётся без изменений.
Порядок таблиц — родители раньше детей (важно для Postgres, где FK включены всегда;
в SQLite `PRAGMA foreign_keys` по умолчанию OFF).
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlmodel import Session, SQLModel, col, select

from app import models as m
from app.db import session_scope
from core import policy as policy_calc
from core.schemas import utcnow

SCHEMA_VERSION = 1
ImportMode = Literal["merge", "replace"]

# Порядок экспорта/импорта: родители раньше детей.
EXPORT_ORDER: tuple[type[SQLModel], ...] = (
    m.Client,
    m.Conversation,
    m.Message,
    m.Deal,
    m.Decision,
    m.Handoff,
    m.Notification,
    m.SecurityIncident,
    m.PolicyVersion,
    m.Event,
    m.Outbox,
)

# Текстовые поля с содержанием диалога/ПДн: в обезличенном дампе вырезаются целиком (Ст. 4).
# Структура (id, роли, фазы, категории, статусы, confidence, latency_ms, ts) сохраняется —
# по ней считаются метрики, но прочитать переписку по дампу нельзя.
REDACTED = "[REDACTED]"
_REDACTED_FIELDS: dict[str, tuple[str, ...]] = {
    "clients": ("name", "phone", "email", "company"),
    "messages": ("text",),
    "deals": ("notes",),
    "decisions": ("action_summary",),
    "notifications": ("text",),
    "outbox": ("text",),
    # Текст инцидента нужен только дашборду безопасности в закрытом контуре:
    # в обезличенном дампе остаются text_hash и pattern — этого хватает для аудита.
    "security_incidents": ("raw_text_masked",),
}
# payload событий тоже может содержать текст клиента — в обезличенном дампе он пустой.
_REDACTED_JSON_FIELDS: dict[str, tuple[str, ...]] = {
    "handoffs": ("summary_json",),
    "events": ("payload",),
}


class ImportReport(BaseModel):
    """Отчёт импорта: что перенесено, что пропущено, где ошибки."""

    schema_version: int
    mode: ImportMode
    dry_run: bool = False
    committed: bool = False
    imported: dict[str, int] = Field(default_factory=dict)
    skipped: dict[str, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    policy_version: int | None = None


class _Abort(Exception):
    """Внутренний сигнал: откатить транзакцию (ошибки или dry-run)."""


@contextmanager
def _use_session(session: Session | None) -> Iterator[Session]:
    """Использует переданную сессию (commit — на вызывающем) или открывает свою."""
    if session is not None:
        yield session
    else:
        with session_scope() as own:
            yield own


# ---------------------------------------------------------------------------
# Экспорт
# ---------------------------------------------------------------------------
def _redact_reason(reason: str) -> str:
    """Оставляет от причины решения только коды («pre_classifier:claim»), без слов клиента."""
    return ":".join(part for part in str(reason).split(":") if part.isascii())[:80]


def _redact_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """Вырезает из строки дампа тексты диалога и ПДн (Ст. 4)."""
    out = dict(row)
    if table == "decisions" and isinstance(out.get("reasons"), list):
        out["reasons"] = [_redact_reason(r) for r in out["reasons"]]
    for field in _REDACTED_FIELDS.get(table, ()):
        if out.get(field):
            out[field] = REDACTED
    for field in _REDACTED_JSON_FIELDS.get(table, ()):
        if out.get(field):
            out[field] = {}
    return out


def export_state(*, mask: bool = False, session: Session | None = None) -> dict[str, Any]:
    """Снимок состояния БД как JSON-совместимый dict.

    Все таблицы читаются в ОДНОЙ транзакции — снимок консистентен (диалог не «порвётся»
    между messages и decisions).
    mask=True — обезличить (Ст. 4): тексты диалога и ПДн вырезаются, структура и метрики
    остаются. Такой дамп годится для аналитики и передачи наружу, но НЕ для восстановления.
    """
    with _use_session(session) as s:
        tables: dict[str, list[dict[str, Any]]] = {}
        for model in EXPORT_ORDER:
            name = model.__tablename__
            rows = [
                row.model_dump(mode="json")
                for row in s.exec(select(model).order_by(col(model.id))).all()
            ]
            tables[name] = [_redact_row(name, row) for row in rows] if mask else rows
    return {
        "app": "ai-native-crm",
        "schema_version": SCHEMA_VERSION,
        "exported_at": utcnow().isoformat(),
        "masked": mask,
        "counts": {name: len(rows) for name, rows in tables.items()},
        "tables": tables,
    }


def export_state_to_file(path: str | Path, *, mask: bool = False) -> dict[str, int]:
    """Пишет дамп в файл (UTF-8, без экранирования кириллицы — дамп читаемый).

    Файл без маскирования содержит ПДн: хранить только в закрытом контуре (Ст. 4).
    """
    dump = export_state(mask=mask)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return dump["counts"]


# ---------------------------------------------------------------------------
# Импорт
# ---------------------------------------------------------------------------
def _short(exc: ValidationError) -> str:
    """Короткое описание ошибки без текста клиента (в отчёт не должны попасть ПДн)."""
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['type']}" for err in exc.errors()[:3]
    )


def _apply_latest_policy(session: Session, rows: list[dict[str, Any]]) -> int:
    """Применяет последнюю политику из дампа как НОВУЮ версию (Ст. 2).

    История версий не перезаписывается: иначе поедут ссылки `policy_version`
    в уже принятых решениях и админка потеряет аудит изменений.
    """
    latest = max(rows, key=lambda row: int(row.get("version", 0)))
    candidate = policy_calc.Policy.model_validate(latest["policy_json"])
    current = policy_calc.get_policy(session)
    if candidate.model_dump(exclude={"version"}) == current.model_dump(exclude={"version"}):
        return current.version
    return policy_calc.update_policy(
        candidate, author="import", note="политика из дампа", session=session
    ).version


def import_state(
    data: dict[str, Any],
    *,
    mode: ImportMode = "merge",
    policy: bool = False,
    dry_run: bool = False,
    session: Session | None = None,
) -> ImportReport:
    """Загружает дамп в текущую БД.

    mode="merge"   — строки с тем же id обновляются, новые добавляются (идемпотентно,
                     повторный импорт того же дампа ничего не меняет);
    mode="replace" — таблицы дампа предварительно очищаются (данные вне дампа не трогаются);
    policy=True    — последняя политика из дампа применяется как новая версия (см. выше);
                     policy=False — история политик пропускается, текущая остаётся как есть;
    dry_run=True   — всё выполняется и откатывается: видно ошибки, БД не изменена.

    Импорт атомарный: при любой ошибке откатывается целиком.
    """
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"несовместимая версия дампа: {version!r}, ожидается {SCHEMA_VERSION}")
    if data.get("masked"):
        raise ValueError("маскированный дамп нельзя импортировать: тексты и ПДн скрыты")
    tables: dict[str, list[dict[str, Any]]] = data.get("tables") or {}

    report = ImportReport(schema_version=SCHEMA_VERSION, mode=mode, dry_run=dry_run)
    with _use_session(session) as s:
        try:
            if mode == "replace":
                for model in reversed(EXPORT_ORDER):
                    if model is m.PolicyVersion:
                        continue  # историю политик в replace-режиме тоже не трогаем
                    for row in s.exec(select(model)).all():
                        s.delete(row)
                s.flush()  # порядок удаления — дети раньше родителей

            for model in EXPORT_ORDER:
                name = model.__tablename__
                rows = tables.get(name) or []
                if model is m.PolicyVersion:
                    # История версий НИКОГДА не переносится построчно: иначе можно
                    # перезаписать чужую версию с тем же номером и потерять аудит (Ст. 2).
                    report.skipped[name] = len(rows)
                    continue
                for index, payload in enumerate(rows):
                    try:
                        obj = model.model_validate(payload)
                    except ValidationError as exc:
                        report.errors.append(f"{name}[{index}]: {_short(exc)}")
                        continue
                    s.merge(obj)  # merge по primary key → идемпотентность
                    report.imported[name] = report.imported.get(name, 0) + 1

            if policy and tables.get("policy_versions"):
                report.policy_version = _apply_latest_policy(s, tables["policy_versions"])

            if report.errors:
                raise _Abort()
            if dry_run:
                raise _Abort()
        except _Abort:
            s.rollback()
            report.committed = False
            return report
    report.committed = not dry_run
    return report


def import_state_from_file(
    path: str | Path,
    *,
    mode: ImportMode = "merge",
    policy: bool = False,
    dry_run: bool = False,
) -> ImportReport:
    """Импорт дампа из файла."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return import_state(payload, mode=mode, policy=policy, dry_run=dry_run)
