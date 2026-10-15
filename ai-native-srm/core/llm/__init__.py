"""Фабрика LLM-провайдеров и цепочка fallback: выбранный -> другой облачный -> Mock."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from core.config import Settings, get_settings
from core.llm.base import (
    DEFAULT_TIMEOUT_S,
    LLMError,
    LLMInvalidResponse,
    LLMProvider,
    LLMTimeout,
)
from core.llm.gigachat import GigaChatLLM
from core.llm.mock import MockLLM
from core.llm.yandex import YandexGPTLLM

__all__ = [
    "FallbackChain",
    "LLMError",
    "LLMInvalidResponse",
    "LLMProvider",
    "LLMTimeout",
    "MockLLM",
    "build_chain",
    "get_llm_provider",
]

logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm")


class FallbackChain:
    """Пробует провайдеров по очереди в рамках ОБЩЕГО бюджета времени.

    Таймаут обеспечивается здесь (future.result), а не внутри SDK, поэтому он
    одинаков для всех провайдеров. Mock отвечает мгновенно и получает минимум
    времени всегда, чтобы цепочка не оставалась без ответа.
    """

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("FallbackChain требует хотя бы одного провайдера")
        self.providers = providers
        self.name = "→".join(p.name for p in providers)

    def _run(self, method: str, timeout: float, *args: Any, **kwargs: Any) -> Any:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        for provider in self.providers:
            remaining = deadline - time.monotonic()
            budget = max(remaining, 1.0) if provider.name == "mock" else remaining
            if budget <= 0:
                last_error = LLMTimeout("бюджет времени исчерпан")
                continue
            call: Callable[..., Any] = getattr(provider, method)
            future = _EXECUTOR.submit(call, *args, timeout=budget, **kwargs)
            try:
                return future.result(timeout=budget)
            except FutureTimeout:
                future.cancel()
                last_error = LLMTimeout(f"{provider.name}: timeout")
            except LLMError as exc:
                last_error = exc
            except Exception as exc:  # неожиданная ошибка SDK
                last_error = LLMError(f"{provider.name}: {type(exc).__name__}")
            logger.warning("LLM provider %s failed: %s", provider.name, type(last_error).__name__)
        raise last_error or LLMError("нет доступных провайдеров")

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        return self._run(
            "complete_json", timeout, system, user, schema, temperature=temperature
        )

    def complete_text(
        self,
        system: str,
        user: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> str:
        return self._run("complete_text", timeout, system, user, temperature=temperature)


def _gigachat(s: Settings) -> LLMProvider | None:
    if not s.gigachat_credentials:
        return None
    return GigaChatLLM(s.gigachat_credentials, s.gigachat_scope, s.gigachat_model, s.gigachat_verify_ssl)


def _yandex(s: Settings) -> LLMProvider | None:
    if not (s.yandex_api_key and s.yandex_folder_id):
        return None
    return YandexGPTLLM(s.yandex_api_key, s.yandex_folder_id)


def build_chain(settings: Settings | None = None) -> FallbackChain:
    """Цепочка по LLM_PROVIDER: выбранный -> другой облачный (если есть ключи) -> Mock."""
    s = settings or get_settings()
    factories = {"gigachat": _gigachat, "yandex": _yandex}
    order = [s.llm_provider] if s.llm_provider in factories else []
    order += [name for name in factories if name not in order] if order else []
    providers: list[LLMProvider] = []
    for name in order:
        provider = factories[name](s)
        if provider is not None:
            providers.append(provider)
        else:
            logger.info("Провайдер %s пропущен: не заданы ключи", name)
    if s.llm_provider not in factories and s.llm_provider != "mock":
        logger.warning("Неизвестный LLM_PROVIDER=%s, используется mock", s.llm_provider)
    providers.append(MockLLM())
    return FallbackChain(providers)


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Основная точка входа: провайдер (цепочка) по текущим настройкам."""
    return build_chain(settings)
