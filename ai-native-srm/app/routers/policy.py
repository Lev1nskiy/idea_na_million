"""Policy Management по HTTP (Шаг 5): чтение, правка версией, история.

Смена политики вступает в силу немедленно: get_policy() читает актуальную версию из БД
на каждое сообщение, перезапуск не нужен (Ст. 2).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field
from app.db import session_scope
from core.learning import log_event
from core.policy import get_policy, list_policy_versions, update_policy
from core.schemas import Policy

router = APIRouter(prefix="/api/policy", tags=["policy"])


class PolicyUpdateRequest(BaseModel):
    """Частичное обновление: deep merge поверх текущей версии (например, только max_pct)."""

    author: str = Field(default="admin", max_length=100)
    note: str = Field(default="", max_length=300)
    patch: dict[str, Any] = Body(default_factory=dict, description="например: {\"discounts\": {\"max_pct\": 10}}")


@router.get("", response_model=Policy)
def read_policy() -> Policy:
    """Актуальная Policy Matrix (в ответе есть номер версии)."""
    return get_policy()


@router.put("", response_model=None)
def write_policy(body: PolicyUpdateRequest) -> dict[str, Any]:
    """Создаёт НОВУЮ версию политики (история не перезаписывается)."""
    if not body.patch:
        return {"unchanged": True, "version": get_policy().version}
    updated = update_policy(body.patch, author=body.author, note=body.note or "правка из админки")
    with session_scope() as s:
        log_event(s, "policy_updated", None, {"version": updated.version, "fields": sorted(body.patch)})
    return {"version": updated.version, "policy": updated.model_dump()}


@router.get("/history")
def policy_history(limit: int = 50) -> list[dict[str, Any]]:
    """История версий (новые сверху) — для страницы админки."""
    out: list[dict[str, Any]] = []
    for row in list_policy_versions(limit=limit):
        discounts = (row.policy_json or {}).get("discounts", {})
        out.append(
            {
                "version": row.version,
                "author": row.author,
                "note": row.note,
                "max_pct": discounts.get("max_pct"),
                "bundle_max_pct": discounts.get("bundle_max_pct"),
                "ts": row.ts.isoformat(),
            }
        )
    return out
