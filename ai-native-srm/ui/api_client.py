"""Тонкая обёртка над REST API для Streamlit (Шаг 5).

Правило шага: UI не содержит бизнес-логики и не ходит в БД/ядро — только эти функции.
Адрес API берётся из API_URL (по умолчанию http://localhost:8000), токен админ-ручек —
из ADMIN_TOKEN.
"""
from __future__ import annotations

import os
from typing import Any

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
TIMEOUT_S = 20


class ApiError(RuntimeError):
    """Ошибка API с человекочитаемым сообщением (его показывает Streamlit)."""


def _check(resp: requests.Response) -> Any:
    if resp.status_code >= 400:
        detail = resp.text
        try:
            detail = resp.json().get("detail", detail)
        except Exception:  # не-JSON ответ (например, 500 от прокси)
            pass
        raise ApiError(f"{resp.status_code}: {detail}")
    if not resp.content:
        return None
    return resp.json()


def _req(method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None,
         headers: dict[str, str] | None = None) -> Any:
    url = f"{API_URL}{path}"
    try:
        resp = requests.request(
            method, url, params={k: v for k, v in (params or {}).items() if v is not None},
            json=json, headers=headers, timeout=TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise ApiError(f"API недоступен ({API_URL}): {exc}") from exc
    return _check(resp)


# --- Сервис и справочники -------------------------------------------------------------------
def health() -> dict[str, Any]:
    return _req("GET", "/api/health")


def clients() -> list[dict[str, Any]]:
    return _req("GET", "/api/clients")


# --- Клиентский чат -------------------------------------------------------------------------
def send_message(client_id: int, text: str, conversation_id: int | None = None, channel: str = "web") -> dict[str, Any]:
    return _req("POST", "/api/messages", json={
        "client_id": client_id, "text": text, "conversation_id": conversation_id, "channel": channel,
    })


def messages(conversation_id: int, after: int = 0) -> list[dict[str, Any]]:
    return _req("GET", f"/api/conversations/{conversation_id}/messages", params={"after": after})


# --- Дашборд менеджера ----------------------------------------------------------------------
def conversations(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return _req("GET", "/api/conversations", params={"status": status, "limit": limit})


def conversation(conversation_id: int) -> dict[str, Any]:
    return _req("GET", f"/api/conversations/{conversation_id}")


def takeover(conversation_id: int, manager: str) -> dict[str, Any]:
    return _req("POST", f"/api/conversations/{conversation_id}/takeover", json={"manager": manager})


def manager_reply(conversation_id: int, manager: str, text: str) -> dict[str, Any]:
    return _req("POST", f"/api/conversations/{conversation_id}/manager-reply", json={"manager": manager, "text": text})


def resolve(conversation_id: int) -> dict[str, Any]:
    return _req("POST", f"/api/conversations/{conversation_id}/resolve")


def override_decision(decision_id: int, manager: str, reason: str) -> dict[str, Any]:
    return _req("POST", f"/api/decisions/{decision_id}/override", json={"manager": manager, "reason": reason})


# --- Уведомления ----------------------------------------------------------------------------
def notifications(unread_only: bool = False, active_only: bool = True, type: str | None = None) -> list[dict[str, Any]]:
    return _req("GET", "/api/notifications", params={"unread_only": unread_only, "active_only": active_only, "type": type})


def mark_notification_read(notification_id: int) -> dict[str, Any]:
    return _req("POST", f"/api/notifications/{notification_id}/read")


def mark_all_notifications_read() -> dict[str, Any]:
    return _req("POST", "/api/notifications/read-all")


# --- Политика -------------------------------------------------------------------------------
def policy() -> dict[str, Any]:
    return _req("GET", "/api/policy")


def update_policy(patch: dict[str, Any], author: str = "admin", note: str = "") -> dict[str, Any]:
    return _req("PUT", "/api/policy", json={"author": author, "note": note, "patch": patch})


def policy_history() -> list[dict[str, Any]]:
    return _req("GET", "/api/policy/history")


# --- Метрики, решения, сделки ---------------------------------------------------------------
def metrics() -> dict[str, Any]:
    return _req("GET", "/api/metrics")


def decisions(conversation_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    return _req("GET", "/api/decisions", params={"conversation_id": conversation_id, "limit": limit})


def incidents(limit: int = 100) -> list[dict[str, Any]]:
    return _req("GET", "/api/security/incidents", params={"limit": limit})


def deals(stage: str | None = None) -> list[dict[str, Any]]:
    return _req("GET", "/api/deals", params={"stage": stage})


# --- Перенос данных (админ-токен, Ст. 4) ----------------------------------------------------
def admin_enabled() -> bool:
    return bool(ADMIN_TOKEN)


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": ADMIN_TOKEN}


def export_dump(mask: bool = True) -> str:
    resp = requests.get(
        f"{API_URL}/api/admin/export", params={"mask": str(mask).lower()},
        headers=_admin_headers(), timeout=TIMEOUT_S,
    )
    if resp.status_code >= 400:
        raise ApiError(f"{resp.status_code}: {resp.text[:200]}")
    return resp.text


def import_dump(content: bytes, mode: str = "merge", policy: bool = False, dry_run: bool = True) -> dict[str, Any]:
    resp = requests.post(
        f"{API_URL}/api/admin/import",
        params={"mode": mode, "policy": str(policy).lower(), "dry_run": str(dry_run).lower()},
        files={"file": ("state.json", content, "application/json")},
        headers=_admin_headers(), timeout=TIMEOUT_S,
    )
    return _check(resp)
