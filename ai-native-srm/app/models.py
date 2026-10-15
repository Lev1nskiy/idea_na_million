"""SQLModel-таблицы (SQLite). Время — naive UTC."""
from typing import Any, Optional

from pydantic import NaiveDatetime
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from core.schemas import utcnow


class Client(SQLModel, table=True):
    __tablename__ = "clients"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    status: str = "new"  # new | loyal | vip | corporate
    telegram_id: Optional[str] = Field(default=None, index=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.id", index=True)
    channel: str = "web"
    status: str = Field(default="ACTIVE", index=True)  # ACTIVE|HOLD|HANDOFF|RESOLVED
    suspicious: bool = False
    discount_offer_made: bool = False
    discount_push_count: int = 0
    low_conf_streak: int = 0
    category: Optional[str] = None  # максимум по приоритету за диалог
    assigned_manager: Optional[str] = None
    last_ai_ping_ts: Optional[NaiveDatetime] = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversations.id", index=True)
    role: str  # client | ai | manager
    text: str
    phase: Optional[str] = None  # ACK|INTERIM|FINAL|HOLD
    deterministic: bool = False
    latency_ms: Optional[int] = None
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    ts: NaiveDatetime = Field(default_factory=utcnow)


class Deal(SQLModel, table=True):
    """Карточка сделки, заполняется ИИ автоматически."""

    __tablename__ = "deals"

    id: Optional[int] = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="clients.id", index=True)
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id")
    product: Optional[str] = None
    qty: Optional[int] = None
    budget: Optional[float] = None
    discount_pct: Optional[float] = None
    stage: str = "new"
    notes: Optional[str] = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    updated_at: NaiveDatetime = Field(default_factory=utcnow)


class Decision(SQLModel, table=True):
    """Журнал автономных решений ИИ (доступен для override, Ст. 5)."""

    __tablename__ = "decisions"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversations.id", index=True)
    message_id: Optional[int] = Field(default=None, foreign_key="messages.id")
    category: str
    route: str
    confidence: float = 0.0
    policy_version: int = 0
    reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    action_summary: Optional[str] = None
    overridden_by: Optional[str] = None
    override_reason: Optional[str] = None
    overridden_at: Optional[NaiveDatetime] = None
    ts: NaiveDatetime = Field(default_factory=utcnow)


class Handoff(SQLModel, table=True):
    __tablename__ = "handoffs"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversations.id", index=True)
    summary_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "PENDING"  # PENDING | TAKEN | CLOSED
    taken_by: Optional[str] = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    taken_at: Optional[NaiveDatetime] = None


class Notification(SQLModel, table=True):
    __tablename__ = "notifications"

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str  # DISPUTE | SECURITY | OVERRIDE_DUE | REMINDER
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id", index=True)
    text: str = ""
    read: bool = False
    active: bool = True  # одно активное уведомление на диалог, дублируется до takeover
    reminder_level: int = 0
    last_notified_at: NaiveDatetime = Field(default_factory=utcnow)
    created_at: NaiveDatetime = Field(default_factory=utcnow)


class SecurityIncident(SQLModel, table=True):
    __tablename__ = "security_incidents"

    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id", index=True)
    raw_text_masked: str  # только маскированный текст (Ст. 4)
    text_hash: str
    pattern: Optional[str] = None
    ts: NaiveDatetime = Field(default_factory=utcnow)


class PolicyVersion(SQLModel, table=True):
    __tablename__ = "policy_versions"

    id: Optional[int] = Field(default=None, primary_key=True)
    version: int = Field(index=True, unique=True)
    policy_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    author: str = "system"
    note: str = ""
    ts: NaiveDatetime = Field(default_factory=utcnow)


class Event(SQLModel, table=True):
    """Лог для LEARNING: решения, блокировки валидаторов, метрики."""

    __tablename__ = "events"

    id: Optional[int] = Field(default=None, primary_key=True)
    type: str = Field(index=True)
    conversation_id: Optional[int] = Field(default=None, index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # уже замаскирован
    latency_ms: Optional[int] = None
    ts: NaiveDatetime = Field(default_factory=utcnow)


class Outbox(SQLModel, table=True):
    """Исходящие сообщения клиенту; каналы (web/Telegram) забирают их поллингом."""

    __tablename__ = "outbox"

    id: Optional[int] = Field(default=None, primary_key=True)
    channel: str = "web"
    conversation_id: int = Field(foreign_key="conversations.id", index=True)
    text: str
    phase: Optional[str] = None
    delivered: bool = False
    ts: NaiveDatetime = Field(default_factory=utcnow)


class User(SQLModel, table=True):
    """Сотрудник CRM (авторизация сайта): менеджер или администратор."""

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    login: str = Field(index=True, unique=True)
    password_hash: str
    name: str = ""
    role: str = Field(default="manager", index=True)  # manager | admin
    active: bool = Field(default=True)
    created_by: Optional[str] = None
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    last_login_at: Optional[NaiveDatetime] = None


class UserSession(SQLModel, table=True):
    """Сессия сотрудника: токен в httpOnly-cookie, TTL и отзыв при выходе/деактивации."""

    __tablename__ = "user_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(index=True, unique=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    created_at: NaiveDatetime = Field(default_factory=utcnow)
    expires_at: NaiveDatetime = Field(default_factory=utcnow)
    revoked_at: Optional[NaiveDatetime] = None
    user_agent: str = ""
