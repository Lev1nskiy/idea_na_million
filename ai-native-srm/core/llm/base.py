"""Базовые типы LLM-слоя: Protocol, ошибки, извлечение JSON, общий класс для SDK-провайдеров."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

DEFAULT_TIMEOUT_S = 2.5


class LLMError(Exception):
    """Любая ошибка провайдера (сеть, авторизация, квота)."""


class LLMTimeout(LLMError):
    """Провайдер не уложился в отведённое время."""


class LLMInvalidResponse(LLMError):
    """Ответ получен, но это не валидный JSON / не нужная структура."""


@runtime_checkable
class LLMProvider(Protocol):
    """Единый интерфейс провайдеров (GigaChat, YandexGPT, Mock, цепочка fallback)."""

    name: str

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> dict[str, Any]: ...

    def complete_text(
        self,
        system: str,
        user: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> str: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json(text: str) -> dict[str, Any]:
    """Достаёт JSON-объект из ответа модели (убирает ```-ограждения и пояснения)."""
    cleaned = _FENCE.sub("", text.strip()).strip()
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise LLMInvalidResponse("ответ модели не содержит JSON-объект")


def json_instruction(schema: dict[str, Any] | None) -> str:
    """Добавка к system-промпту для провайдеров без нативного JSON-режима."""
    text = "\n\nОтветь ТОЛЬКО валидным JSON-объектом без пояснений и без markdown-ограждений."
    if schema:
        text += "\nФормат ответа:\n" + json.dumps(schema, ensure_ascii=False)
    return text


class BaseLLM(ABC):
    """Провайдер, реализующий только _chat(); JSON строится поверх текста."""

    name = "base"

    @abstractmethod
    def _chat(self, system: str, user: str, temperature: float, timeout: float) -> str:
        """Один запрос к модели, возвращает текст ответа."""

    def complete_text(
        self,
        system: str,
        user: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> str:
        return self._chat(system, user, temperature, timeout).strip()

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        raw = self._chat(system + json_instruction(schema), user, temperature, timeout)
        return extract_json(raw)
