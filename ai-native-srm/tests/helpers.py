"""Общие помощники для сквозных тестов оркестратора."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlmodel import select

from app import db as dbm
from app import models as m
from core import ai_engine
from core.llm import MockLLM
from core.schemas import BotResponse, IncomingMessage


def send(text: str, client_id: int = 1, conv_id: int | None = None, llm: Any = None) -> BotResponse:
    """Отправляет сообщение клиента и дожидается фоновых задач (после INTERIM)."""

    async def go() -> BotResponse:
        resp = await ai_engine.process_message(
            IncomingMessage(client_id=client_id, conversation_id=conv_id, text=text), llm=llm or MockLLM()
        )
        await ai_engine.drain_background()
        return resp

    return asyncio.run(go())


def rows(model: Any, **filters: Any) -> list[Any]:
    with dbm.session_scope() as s:
        stmt = select(model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(model, key) == value)
        return list(s.exec(stmt).all())


def ai_messages(conv_id: int) -> list[m.Message]:
    return rows(m.Message, conversation_id=conv_id, role="ai")


def conv(conv_id: int) -> m.Conversation:
    with dbm.session_scope() as s:
        row = s.get(m.Conversation, conv_id)
        assert row is not None
        return row


class NoLLM:
    """Любой вызов LLM — ошибка теста (путь должен обойтись без модели)."""

    name = "nollm"

    def complete_json(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("LLM не должна вызываться")

    complete_text = complete_json


class ScriptedText(MockLLM):
    """MockLLM для понимания; тексты ответов берутся из сценария (str или исключение)."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete_text(self, system: str, user: str, *, timeout: float = 2.5, temperature: float = 0.0) -> str:
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class ScriptedJSON:
    """Провайдер для handoff: возвращает заданный JSON."""

    name = "scripted-json"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def complete_json(self, *a: Any, **k: Any) -> dict[str, Any]:
        return self.payload

    def complete_text(self, *a: Any, **k: Any) -> str:
        return ""
