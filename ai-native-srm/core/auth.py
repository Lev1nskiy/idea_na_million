"""Учётные записи сотрудников и сессии (роли manager/admin).

Модель угроз простая и честная: пароли сотрудников проверяются **на бэкенде**
(PBKDF2-HMAC-SHA256, соль на пользователя), а на клиент выдаётся случайный токен сессии.
Токен живёт в httpOnly-cookie сайта, доезжает до API в заголовке `X-Auth-Token`
(или `Authorization: Bearer`) и хранится в БД только как SHA-256 — утечка таблицы
сессий не даёт готовых ключей.

Роли: `manager` — обычный сотрудник (диалоги, уведомления, метрики);
`admin` — то же плюс политика и управление учётками. Ограничения прав проверяются
здесь (`current_user`/`admin_user`), а не в интерфейсе: интерфейс лишь не показывает
то, что всё равно запрещено.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import re
from datetime import timedelta
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app import db as dbm
from app import models as m
from core.schemas import utcnow

ROLE_MANAGER = "manager"
ROLE_ADMIN = "admin"
ROLES = (ROLE_MANAGER, ROLE_ADMIN)

SESSION_TTL_HOURS = 12
_PBKDF2_ITERATIONS = 200_000
_LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD_LEN = 5

DEMO_USERS = (
    # логин, пароль, имя, роль — демо-учётки первого запуска (меняются в интерфейсе)
    ("admin", "admin", "Администратор", ROLE_ADMIN),
    ("manager", "manager", "Менеджер А.", ROLE_MANAGER),
)


# ---------------------------------------------------------------------------
# Пароли
# ---------------------------------------------------------------------------
def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """PBKDF2-HMAC-SHA256: `pbkdf2_sha256$итерации$соль_hex$хеш_hex`."""
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Сравнение в постоянном времени; на битый формат хеша отвечаем `False`, а не падаем."""
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def validate_login(login: str) -> str:
    value = (login or "").strip().lower()
    if not _LOGIN_RE.match(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Логин: 3–32 символа, латиница/цифры, начинается с буквы или цифры",
        )
    return value


def validate_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Пароль: минимум {MIN_PASSWORD_LEN} символов",
        )
    return password


# ---------------------------------------------------------------------------
# Сессии
# ---------------------------------------------------------------------------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_session(session: Session, user: m.User, *, user_agent: str = "") -> tuple[str, Any]:
    """Выдаёт токен сессии (в БД — только его хеш) и обновляет `last_login_at`."""
    purge_expired(session)
    token = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(hours=SESSION_TTL_HOURS)
    session.add(
        m.UserSession(token=_token_hash(token), user_id=int(user.id or 0), expires_at=expires, user_agent=user_agent[:200])
    )
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    return token, expires


def resolve_token(session: Session, token: str | None) -> m.User | None:
    """Токен → активный пользователь (или None: нет токена, протух, отозван, деактивирован)."""
    if not token:
        return None
    row = session.exec(select(m.UserSession).where(m.UserSession.token == _token_hash(token))).first()
    if row is None or row.revoked_at is not None or row.expires_at < utcnow():
        return None
    user = session.get(m.User, row.user_id)
    if user is None or not user.active:
        return None
    return user


def revoke_session(session: Session, token: str | None) -> bool:
    """Выход из аккаунта: сессия помечается отозванной (остаётся в журнале)."""
    if not token:
        return False
    row = session.exec(select(m.UserSession).where(m.UserSession.token == _token_hash(token))).first()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = utcnow()
    session.add(row)
    session.commit()
    return True


def revoke_user_sessions(session: Session, user_id: int) -> int:
    """Гасит все сессии сотрудника: при смене пароля, смене роли и деактивации."""
    rows = session.exec(
        select(m.UserSession).where(m.UserSession.user_id == user_id, m.UserSession.revoked_at.is_(None))  # type: ignore[union-attr]
    ).all()
    for row in rows:
        row.revoked_at = utcnow()
        session.add(row)
    if rows:
        session.commit()
    return len(rows)


def purge_expired(session: Session) -> int:
    """Уборка мусора: просроченные сессии удаляются при следующем входе."""
    rows = session.exec(select(m.UserSession).where(m.UserSession.expires_at < utcnow())).all()
    for row in rows:
        session.delete(row)
    if rows:
        session.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# FastAPI-зависимости
# ---------------------------------------------------------------------------
def _extract_token(request: Request) -> str | None:
    header = request.headers.get("x-auth-token")
    if not header:
        auth = request.headers.get("authorization") or ""
        header = auth[7:] if auth.lower().startswith("bearer ") else auth
    return (header or "").strip() or None


def current_user(request: Request, session: Session = Depends(dbm.get_session)) -> m.User:
    """Текущий сотрудник или 401 (с подсказкой для UI — куда идти логиниться)."""
    user = resolve_token(session, _extract_token(request))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Нужна авторизация: войдите по логину и паролю",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def admin_user(user: m.User = Depends(current_user)) -> m.User:
    """Политика и управление учётками — только администратор."""
    if user.role != ROLE_ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Доступно только администратору")
    return user


def public_user(user: m.User | None) -> dict[str, Any]:
    """Пользователь в форме сайта (хеша пароля здесь нет и быть не может)."""
    if user is None:
        return {}
    return {
        "id": str(user.id),
        "login": user.login,
        "name": user.name or user.login,
        "role": user.role,
        "isActive": bool(user.active),
        "createdAt": user.created_at.isoformat(timespec="seconds") + "Z",
        "lastLoginAt": (user.last_login_at.isoformat(timespec="seconds") + "Z") if user.last_login_at else None,
    }


# ---------------------------------------------------------------------------
# Первичные учётки
# ---------------------------------------------------------------------------
def count_admins(session: Session, *, exclude_id: int | None = None) -> int:
    rows = session.exec(select(m.User).where(m.User.role == ROLE_ADMIN, m.User.active.is_(True))).all()  # type: ignore[union-attr]
    return len([r for r in rows if r.id != exclude_id])


def ensure_seed_users(session: Session) -> bool:
    """Создаёт демо-учётки, если сотрудников ещё нет.

    Пароли можно переопределить окружением (`ADMIN_PASSWORD`, `MANAGER_PASSWORD`) —
    чтобы на демо-стенде не оставалось пароля «admin» по умолчанию, если он не нужен.
    """
    if session.exec(select(m.User)).first() is not None:
        return False
    env_passwords = {ROLE_ADMIN: os.getenv("ADMIN_PASSWORD"), ROLE_MANAGER: os.getenv("MANAGER_PASSWORD")}
    for login, password, name, role in DEMO_USERS:
        session.add(
            m.User(
                login=login,
                password_hash=hash_password(env_passwords.get(role) or password),
                name=name,
                role=role,
                created_by="seed",
            )
        )
    session.commit()
    return True
