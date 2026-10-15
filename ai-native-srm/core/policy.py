"""Policy Management: версионируемая Policy Matrix (Ст. 2).

Ключевое правило: get_policy() читает актуальную версию из БД при КАЖДОМ вызове
(никакого кэша), поэтому изменения применяются мгновенно, без перезапуска.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from app.db import session_scope
from app.models import PolicyVersion
from core.config import DATA_DIR
from core.schemas import Compensation, Policy, SlaInfo


@contextmanager
def _use_session(session: Session | None) -> Iterator[Session]:
    """Использует переданную сессию (commit — на вызывающем) или открывает свою."""
    if session is not None:
        yield session
    else:
        with session_scope() as own:
            yield own


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивное слияние dict; списки и скаляры заменяются целиком."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _load_seed() -> Policy:
    with open(DATA_DIR / "policy_seed.json", encoding="utf-8") as f:
        return Policy.model_validate(json.load(f))


def _latest_row(session: Session) -> PolicyVersion | None:
    stmt = select(PolicyVersion).order_by(PolicyVersion.version.desc())  # type: ignore[attr-defined]
    return session.exec(stmt).first()


def _insert_version(session: Session, policy: Policy, version: int, author: str, note: str) -> PolicyVersion:
    payload = policy.model_dump(exclude={"version"})
    row = PolicyVersion(version=version, policy_json=payload, author=author, note=note)
    session.add(row)
    session.flush()
    return row


def seed_policy_if_empty(session: Session | None = None) -> bool:
    """Создаёт версию 1 из policy_seed.json, если политик в БД ещё нет."""
    with _use_session(session) as s:
        if _latest_row(s) is not None:
            return False
        _insert_version(s, _load_seed(), 1, "system", "seed")
        return True


def get_policy(session: Session | None = None) -> Policy:
    """Актуальная версия Policy Matrix (читается из БД при каждом вызове)."""
    with _use_session(session) as s:
        row = _latest_row(s)
        if row is None:
            row = _insert_version(s, _load_seed(), 1, "system", "seed")
        policy = Policy.model_validate(row.policy_json)
        policy.version = row.version
        return policy


def get_policy_version(session: Session | None = None) -> int:
    return get_policy(session).version


def update_policy(
    new: Policy | dict[str, Any],
    author: str,
    note: str = "",
    session: Session | None = None,
) -> Policy:
    """Создаёт НОВУЮ версию политики.

    dict трактуется как частичное обновление (deep merge поверх текущей версии),
    например: update_policy({"discounts": {"max_pct": 10}}, author="admin").
    Если передана внешняя session — commit остаётся на вызывающем.
    """
    with _use_session(session) as s:
        current = get_policy(s)
        if isinstance(new, dict):
            merged = _deep_merge(current.model_dump(exclude={"version"}), new)
            candidate = Policy.model_validate(merged)  # валидация диапазонов
        else:
            candidate = Policy.model_validate(new.model_dump(exclude={"version"}))
        next_version = current.version + 1
        _insert_version(s, candidate, next_version, author, note)
        candidate.version = next_version
        return candidate


def list_policy_versions(limit: int = 50, session: Session | None = None) -> list[PolicyVersion]:
    """История версий (новые сверху) для админки."""
    with _use_session(session) as s:
        stmt = (
            select(PolicyVersion)
            .order_by(PolicyVersion.version.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(s.exec(stmt).all())


# ---------------------------------------------------------------------------
# Расчёты по Policy (чистые функции: policy передаётся явно)
# ---------------------------------------------------------------------------
def calc_allowed_discount(
    policy: Policy,
    qty: int,
    order_sum: float,
    client_status: str = "new",
    is_bundle: bool = False,
) -> float:
    """Максимально допустимая скидка (%) для заказа.

    База: наибольший порог из tiers, где order_sum СТРОГО больше min_sum;
    при комплекте — не ниже bundle_max_pct; постоянным клиентам добавляется
    loyal_bonus_pct. Итог всегда ограничен discounts.max_pct.
    qty зарезервирован под пороги по количеству (в seed не используется).
    """
    d = policy.discounts
    base = 0.0
    for tier in d.tiers:
        if order_sum > tier.min_sum:
            base = max(base, tier.pct)
    if is_bundle:
        base = max(base, d.bundle_max_pct)
    if client_status in d.loyal_statuses:
        base += d.loyal_bonus_pct
    return max(0.0, min(base, d.max_pct))


def calc_delivery_compensation(policy: Policy, delay_days: int, order_sum: float) -> Compensation:
    """Компенсация за задержку: правило с наибольшим pct среди сработавших."""
    pct = 0.0
    for rule in policy.delivery_compensation:
        if delay_days > rule.delay_days_gt:
            pct = max(pct, rule.pct_of_sum)
    return Compensation(pct=pct, amount=round(order_sum * pct / 100, 2))


def get_manager_sla(policy: Policy, now: datetime | None = None) -> SlaInfo:
    """Честный срок ответа менеджера с учётом рабочих часов (Ст. 6, 8, 9).

    now: naive трактуется как UTC; aware конвертируется в UTC.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)

    sla = policy.manager_sla
    wh = sla.work_hours
    local = now + timedelta(hours=wh.utc_offset_hours)
    is_work = local.isoweekday() in wh.days and wh.start_hour <= local.hour < wh.end_hour
    return SlaInfo(
        is_work_time=is_work,
        response_hours=sla.response_hours if is_work else sla.off_hours_response_hours,
        start_hour=wh.start_hour,
        end_hour=wh.end_hour,
        days=list(wh.days),
    )
