"""Подключение к БД (SQLite по умолчанию), сессии, инициализация схемы."""
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from core.config import get_settings

_engine: Engine | None = None


def _make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def get_engine() -> Engine:
    """Ленивая инициализация engine из настроек."""
    global _engine
    if _engine is None:
        _engine = _make_engine(get_settings().db_url)
    return _engine


def configure_engine(url: str) -> Engine:
    """Переключает engine на другой URL (используется в тестах)."""
    global _engine
    dispose_engine()
    _engine = _make_engine(url)
    return _engine


def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
        _engine = None


def init_db() -> None:
    """Создаёт таблицы, если их ещё нет, и заводит демо-учётки сотрудников при пустой базе."""
    from app import models  # noqa: F401  (регистрация таблиц в metadata)
    from core.auth import ensure_seed_users

    SQLModel.metadata.create_all(get_engine())
    with session_scope() as session:
        ensure_seed_users(session)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Сессия с commit/rollback. Объекты остаются доступны после commit."""
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI-зависимость."""
    with session_scope() as session:
        yield session
