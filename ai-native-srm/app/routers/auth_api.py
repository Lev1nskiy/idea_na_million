"""Ручки авторизации сайта: вход, выход, «кто я» и управление сотрудниками.

Почему это здесь, а не в мосте `/api/site/*`: вход — отдельная тема со своими правилами
(публичный логин, роли, отзыв сессий), и смешивать её с адаптером данных неудобно.
Пути те же (`/api/site/...`), поэтому сайту не нужно знать про два разных префикса.

Разделение доступа:
* `POST /api/site/auth/login` — публичный (иначе некуда входить);
* `logout`/`me` — любой авторизованный сотрудник;
* `users*` — только администратор: он создаёт логины и пароли новым менеджерам,
  меняет пароль, роль и отключает учётку.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app import db as dbm
from app import models as m
from core import auth

public_router = APIRouter(prefix="/api/site/auth", tags=["site-auth"])
router = APIRouter(prefix="/api/site/auth", tags=["site-auth"], dependencies=[Depends(auth.current_user)])
users_router = APIRouter(prefix="/api/site/users", tags=["site-users"], dependencies=[Depends(auth.admin_user)])


class LoginBody(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class UserCreateBody(BaseModel):
    login: str
    password: str
    name: str = ""
    role: str = auth.ROLE_MANAGER


class UserPatchBody(BaseModel):
    name: str | None = None
    role: str | None = None
    password: str | None = None
    isActive: bool | None = None


# ---------------------------------------------------------------------------
# Вход / выход / текущий пользователь
# ---------------------------------------------------------------------------
@public_router.post("/login")
def login(body: LoginBody, request: Request, session: Session = Depends(dbm.get_session)) -> dict[str, Any]:
    """Проверка пары логин-пароль и выдача токена сессии.

    Одинаковый текст ошибки для «нет такого логина» и «неверный пароль» — чтобы по ответу
    нельзя было перечислить существующие учётки.
    """
    login_value = (body.login or "").strip().lower()
    user = session.exec(select(m.User).where(m.User.login == login_value)).first()
    if user is None or not auth.verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Неверный логин или пароль")
    if not user.active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Учётка отключена администратором")

    token, expires = auth.start_session(session, user, user_agent=request.headers.get("user-agent", ""))
    return {"token": token, "expiresAt": expires.isoformat(timespec="seconds") + "Z", "user": auth.public_user(user)}


@router.post("/logout")
def logout(request: Request, session: Session = Depends(dbm.get_session)) -> dict[str, Any]:
    """Выход: сессия отзывается, cookie на стороне сайта очищается."""
    token = auth._extract_token(request)  # noqa: SLF001 — тот же разбор заголовков, что и в зависимостях
    return {"ok": auth.revoke_session(session, token)}


@router.get("/me")
def me(user: m.User = Depends(auth.current_user)) -> dict[str, Any]:
    """Кто вошёл: интерфейс по этой ручке рисует роль и решает, показывать ли раздел политики."""
    return {"user": auth.public_user(user)}


# ---------------------------------------------------------------------------
# Сотрудники (только администратор)
# ---------------------------------------------------------------------------
@users_router.get("")
def list_users(session: Session = Depends(dbm.get_session)) -> dict[str, Any]:
    rows = session.exec(select(m.User).order_by(m.User.id)).all()  # type: ignore[attr-defined]
    return {"users": [auth.public_user(u) for u in rows]}


@users_router.post("")
def create_user(body: UserCreateBody, admin: m.User = Depends(auth.admin_user), session: Session = Depends(dbm.get_session)) -> dict[str, Any]:
    """Администратор заводит нового сотрудника: логин, пароль и роль."""
    login_value = auth.validate_login(body.login)
    auth.validate_password(body.password)
    role = body.role if body.role in auth.ROLES else auth.ROLE_MANAGER
    if session.exec(select(m.User).where(m.User.login == login_value)).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Такой логин уже занят")

    user = m.User(
        login=login_value,
        password_hash=auth.hash_password(body.password),
        name=(body.name or "").strip() or login_value,
        role=role,
        created_by=admin.login,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"user": auth.public_user(user)}


def _one(session: Session, user_id: int) -> m.User:
    user = session.get(m.User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сотрудник не найден")
    return user


@users_router.patch("/{user_id}")
def patch_user(
    user_id: int,
    body: UserPatchBody,
    admin: m.User = Depends(auth.admin_user),
    session: Session = Depends(dbm.get_session),
) -> dict[str, Any]:
    """Имя, роль, пароль и активность учётки. Сессии сбрасываются на любом таком изменении.

    Две защиты от потери доступа к системе: нельзя менять роль и активность самому себе
    и нельзя оставить систему без активного администратора.
    """
    user = _one(session, user_id)
    touches_access = body.role is not None or body.isActive is not None or body.password is not None

    if user.id == admin.id and (body.role is not None or body.isActive is not None):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Свою роль и активность менять нельзя")

    if body.role is not None:
        if body.role not in auth.ROLES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Роль: manager или admin")
        if user.role == auth.ROLE_ADMIN and body.role != auth.ROLE_ADMIN and auth.count_admins(session, exclude_id=int(user.id or 0)) == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="В системе должен остаться администратор")
        user.role = body.role

    if body.isActive is not None:
        if not body.isActive and user.role == auth.ROLE_ADMIN and auth.count_admins(session, exclude_id=int(user.id or 0)) == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="В системе должен остаться администратор")
        user.active = body.isActive

    if body.name is not None:
        user.name = body.name.strip() or user.login

    if body.password is not None:
        auth.validate_password(body.password)
        user.password_hash = auth.hash_password(body.password)

    session.add(user)
    session.commit()
    session.refresh(user)

    if touches_access:
        auth.revoke_user_sessions(session, int(user.id or 0))  # старые токены больше не действуют
    return {"user": auth.public_user(user)}


@users_router.delete("/{user_id}")
def deactivate_user(
    user_id: int,
    admin: m.User = Depends(auth.admin_user),
    session: Session = Depends(dbm.get_session),
) -> dict[str, Any]:
    """Отключение учётки (без удаления строки — история остаётся)."""
    return patch_user(user_id, UserPatchBody(isActive=False), admin, session)  # type: ignore[arg-type]
