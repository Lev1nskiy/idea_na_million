"""Авторизация сотрудников: роли, сессии и управление учётками (`/api/site/auth`, `/api/site/users`).

Проверяем не «ручка отвечает», а правила доступа целиком: вход по логину и паролю,
разделение менеджер/администратор, создание новых учёток администратором, отзыв сессий
при смене пароля/роли/деактивации и то, что секреты не лежат в БД в открытом виде.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import select  # noqa: E402

from app import models as m  # noqa: E402
from app.main import app  # noqa: E402
from core import auth  # noqa: E402


@pytest.fixture()
def api(db):
    with TestClient(app) as client:
        yield client


def login(api: TestClient, login_value: str, password: str):
    return api.post("/api/site/auth/login", json={"login": login_value, "password": password})


def as_user(api: TestClient, login_value: str, password: str) -> dict[str, str]:
    resp = login(api, login_value, password)
    assert resp.status_code == 200, resp.text
    return {"X-Auth-Token": resp.json()["token"]}


# ---------------------------------------------------------------------------
# Демо-учётки и вход
# ---------------------------------------------------------------------------
def test_seed_creates_admin_and_manager(db):
    """Первый запуск заводит две учётки: admin и manager (демо-стенд должен куда-то входить)."""
    with db.session_scope() as s:
        rows = {(u.login, u.role) for u in s.exec(select(m.User)).all()}
    assert rows == {("admin", "admin"), ("manager", "manager")}


def test_login_returns_token_and_user(api):
    body = login(api, "admin", "admin").json()
    assert body["token"] and body["expiresAt"].endswith("Z")
    assert body["user"]["role"] == "admin" and body["user"]["login"] == "admin"

    me = api.get("/api/site/auth/me", headers={"X-Auth-Token": body["token"]})
    assert me.status_code == 200 and me.json()["user"]["login"] == "admin"


def test_login_is_case_insensitive_for_login_only(api):
    """Логин нормализуется (ввод с большой буквы не мешает), пароль — нет."""
    assert login(api, "Admin", "admin").status_code == 200
    assert login(api, "admin", "Admin").status_code == 401


def test_wrong_password_and_unknown_login_are_indistinguishable(api):
    """Один и тот же ответ на «нет такого логина» и «неверный пароль» — учётки не перечислить."""
    wrong = login(api, "admin", "секрет-123")
    unknown = login(api, "nobody", "секрет-123")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


# ---------------------------------------------------------------------------
# Роли
# ---------------------------------------------------------------------------
def test_manager_cannot_touch_policy_or_users(api):
    """Менеджер работает с клиентами, но политика и учётки — зона администратора."""
    headers = as_user(api, "manager", "manager")
    assert api.get("/api/site/conversations", headers=headers).status_code == 200
    assert api.get("/api/site/policy", headers=headers).status_code == 403
    assert api.put("/api/site/policy", json={}, headers=headers).status_code == 403
    assert api.get("/api/site/users", headers=headers).status_code == 403
    assert api.post("/api/site/users", json={"login": "x1", "password": "12345"}, headers=headers).status_code == 403


def test_admin_sees_policy_and_creates_manager(api):
    """Основной сценарий: администратор заводит менеджера, тот входит по своим данным."""
    admin = as_user(api, "admin", "admin")
    assert api.get("/api/site/policy", headers=admin).status_code == 200

    created = api.post(
        "/api/site/users",
        json={"login": "ivan.p", "password": "print-2026", "name": "Иван Петров", "role": "manager"},
        headers=admin,
    )
    assert created.status_code == 200, created.text
    assert created.json()["user"]["login"] == "ivan.p" and created.json()["user"]["role"] == "manager"

    ivan = as_user(api, "ivan.p", "print-2026")  # новый сотрудник входит своими данными
    assert api.get("/api/site/conversations", headers=ivan).status_code == 200
    assert api.get("/api/site/policy", headers=ivan).status_code == 403


def test_login_validation(api):
    """Логин и пароль проверяются до создания учётки, логины не дублируются."""
    admin = as_user(api, "admin", "admin")
    assert api.post("/api/site/users", json={"login": "a", "password": "12345"}, headers=admin).status_code == 400
    assert api.post("/api/site/users", json={"login": "Иван", "password": "12345"}, headers=admin).status_code == 400
    assert api.post("/api/site/users", json={"login": "petr", "password": "123"}, headers=admin).status_code == 400
    assert api.post("/api/site/users", json={"login": "admin", "password": "12345"}, headers=admin).status_code == 409


# ---------------------------------------------------------------------------
# Смена пароля, роли и отключение учётки
# ---------------------------------------------------------------------------
def test_admin_resets_password_and_all_sessions_of_that_user_die(api):
    """Смена пароля закрывает уже открытые сессии сотрудника — старый токен бесполезен."""
    admin = as_user(api, "admin", "admin")
    created = api.post("/api/site/users", json={"login": "olga", "password": "old-pass"}, headers=admin).json()
    user_id = created["user"]["id"]
    old_token = as_user(api, "olga", "old-pass")

    assert login(api, "olga", "new-pass").status_code == 401
    patched = api.patch(f"/api/site/users/{user_id}", json={"password": "new-pass"}, headers=admin)
    assert patched.status_code == 200

    assert api.get("/api/site/conversations", headers=old_token).status_code == 401  # сессия отозвана
    assert login(api, "olga", "old-pass").status_code == 401
    assert login(api, "olga", "new-pass").status_code == 200


def test_deactivated_user_cannot_login_and_session_is_revoked(api):
    admin = as_user(api, "admin", "admin")
    user_id = api.post("/api/site/users", json={"login": "petr", "password": "12345"}, headers=admin).json()["user"]["id"]
    petr = as_user(api, "petr", "12345")

    assert api.delete(f"/api/site/users/{user_id}", headers=admin).status_code == 200
    assert login(api, "petr", "12345").status_code == 403
    assert api.get("/api/site/conversations", headers=petr).status_code == 401


def test_admin_cannot_lock_itself_or_leave_system_without_admin(api):
    """Защита от потери доступа: себя не отключить, последнего администратора не разжаловать."""
    admin = as_user(api, "admin", "admin")
    my_id = api.get("/api/site/auth/me", headers=admin).json()["user"]["id"]

    assert api.patch(f"/api/site/users/{my_id}", json={"isActive": False}, headers=admin).status_code == 400
    assert api.patch(f"/api/site/users/{my_id}", json={"role": "manager"}, headers=admin).status_code == 400
    assert api.patch("/api/site/users/1", json={"role": "manager"}, headers=admin).status_code == 400  # единственный админ


def test_role_change_revokes_sessions(api):
    """Повышение до администратора тоже сбрасывает сессии: права меняются только при новом входе."""
    admin = as_user(api, "admin", "admin")
    user_id = api.post("/api/site/users", json={"login": "rita", "password": "12345"}, headers=admin).json()["user"]["id"]
    token = as_user(api, "rita", "12345")

    assert api.patch(f"/api/site/users/{user_id}", json={"role": "admin"}, headers=admin).status_code == 200
    assert api.get("/api/site/policy", headers=token).status_code == 401  # старая сессия погашена
    fresh = as_user(api, "rita", "12345")
    assert api.get("/api/site/policy", headers=fresh).status_code == 200  # теперь админ


def test_secrets_are_not_stored_in_plain_text(api, db):
    """В БД нет ни открытого пароля, ни открытого токена сессии."""
    token = as_user(api, "admin", "admin")["X-Auth-Token"]
    with db.session_scope() as s:
        user = s.exec(select(m.User).where(m.User.login == "admin")).one()
        session_row = s.exec(select(m.UserSession)).first()

    assert "admin" not in user.password_hash and user.password_hash.startswith("pbkdf2_sha256$")
    assert auth.verify_password("admin", user.password_hash) and not auth.verify_password("admin1", user.password_hash)
    assert session_row is not None and session_row.token != token


def test_expired_session_is_rejected(api, db):
    """Истёкший токен не работает, даже если строку никто не отзывал."""
    token = as_user(api, "manager", "manager")["X-Auth-Token"]
    with db.session_scope() as s:
        row = s.exec(select(m.UserSession)).first()
        assert row is not None
        row.expires_at = row.created_at  # уже протухла
        s.add(row)
    assert api.get("/api/site/conversations", headers={"X-Auth-Token": token}).status_code == 401
