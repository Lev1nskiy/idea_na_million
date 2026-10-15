"""UNDERSTANDING: LLM -> структурированный Understanding (интент, сущности, тональность, confidence)."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from core.llm import LLMError, LLMInvalidResponse, LLMProvider, get_llm_provider
from core.prompts.understanding import SCHEMA_HINT, build_system, build_user_prompt
from core.schemas import CATEGORY_RANK, Entities, Understanding

logger = logging.getLogger(__name__)  # в логи — только типы ошибок, текст клиента не пишем (Ст. 4)


def _num(value: Any, lo: float, hi: float, default: float) -> float:
    """Число в диапазоне [lo, hi]; нечисловое значение — признак невалидного ответа."""
    if value is None:
        return default
    try:
        return min(max(float(value), lo), hi)
    except (TypeError, ValueError) as exc:
        raise LLMInvalidResponse("нечисловое значение в ответе модели") from exc


def _coerce(raw: dict[str, Any]) -> Understanding:
    """Мягкая приводка ответа LLM к контракту Understanding."""
    if not raw or ("intent" not in raw and "category_candidates" not in raw):
        raise LLMInvalidResponse("в ответе нет intent/category_candidates")

    categories: list[str] = []
    for item in raw.get("category_candidates") or []:
        letter = str(item).strip().upper()[:1]
        if letter in CATEGORY_RANK and letter not in categories:
            categories.append(letter)
    categories.sort(key=lambda c: CATEGORY_RANK[c])

    try:
        entities = Entities.model_validate(raw.get("entities") or {})
    except ValidationError:
        entities = Entities()  # битые сущности не должны ронять весь разбор

    return Understanding.model_validate(
        {
            "intent": str(raw.get("intent") or "unknown")[:64],
            "category_candidates": categories,
            "entities": entities,
            "sentiment": _num(raw.get("sentiment"), -1.0, 1.0, 0.0),
            "confidence": _num(raw.get("confidence"), 0.0, 1.0, 0.0),
            "injection_suspected": bool(raw.get("injection_suspected", False)),
            "language": str(raw.get("language") or "ru")[:8],
        }
    )


def analyze(
    text: str,
    history_tail: Sequence[tuple[str, str]] = (),
    llm: LLMProvider | None = None,
    timeout: float = 2.5,
) -> Understanding:
    """Анализирует сообщение клиента.

    text — легитимная часть сообщения (после вырезания инъекции).
    history_tail — [(role, text)] последних сообщений ЭТОГО диалога.
    Невалидный JSON -> 1 повтор; затем fallback Understanding(confidence=0).
    Транспортная ошибка/таймаут -> сразу fallback (без повтора, бюджет времени ограничен).
    """
    provider = llm or get_llm_provider()
    system = build_system()
    user = build_user_prompt(text, history_tail)

    for attempt in (1, 2):
        try:
            raw = provider.complete_json(system, user, SCHEMA_HINT, timeout=timeout, temperature=0.0)
            return _coerce(raw)
        except (LLMInvalidResponse, ValidationError) as exc:
            logger.warning("understanding: невалидный ответ (попытка %d): %s", attempt, type(exc).__name__)
        except LLMError as exc:
            logger.warning("understanding: LLM недоступен: %s", type(exc).__name__)
            break
    return Understanding(confidence=0.0)
