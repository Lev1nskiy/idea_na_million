"""Фабрика LLM-провайдеров и цепочка fallback: API-модель → облака → локальная → Mock.

Порядок задаётся `LLM_PROVIDER`:

* `auto` (по умолчанию) — сначала провайдеры, у которых есть ключи: `api` (любая API-модель
  с OpenAI-совместимым чатом: OpenAI, OpenRouter, DeepSeek, Groq, Qwen…), затем `gigachat`,
  затем `yandex`, затем локальные веса, если задан `LOCAL_MODEL`;
* `api` — своя API-модель первой (у остальных ключей может и не быть);
* `gigachat` / `yandex` — вендорский провайдер первым: остальные остаются следующим шагом;
* `yandex,gigachat` — явный порядок цепочки (через запятую);
* `mock` — только демо-движок, без обращений в сеть.

Поэтому «GigaChat или YandexGPT» — больше не единственный облачный вариант: любая API-модель
включается тремя переменными (`LLM_API_BASE_URL`, `LLM_API_KEY`, `LLM_API_MODEL`) и встаёт
первой в очереди; вендорские провайдеры остаются запасом, а `LLM_PROVIDER=api,mock` убирает их
из цепочки совсем. Mock всегда замыкает цепочку: без него движок остался бы без ответа при
недоступном провайдере.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from core.config import Settings, get_settings
from core.llm.api import AnyApiLLM
from core.llm.base import (
    DEFAULT_TIMEOUT_S,
    LLMError,
    LLMInvalidResponse,
    LLMProvider,
    LLMTimeout,
)
from core.llm.gigachat import GigaChatLLM
from core.llm.local import LocalLLM
from core.llm.mock import MockLLM
from core.llm.yandex import YandexGPTLLM

__all__ = [
    "AnyApiLLM",
    "FallbackChain",
    "LLMError",
    "LLMInvalidResponse",
    "LLMProvider",
    "LLMTimeout",
    "MockLLM",
    "build_chain",
    "describe_llm",
    "get_llm_provider",
    "provider_status",
]

logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm")

#: Провайдеры, которые включаются автоматически, если они настроены (порядок = приоритет).
#: `api` — любая API-модель (OpenAI-совместимый чат): встаёт первой, потому что вендор не важен,
#: важен только эндпоинт. `local` — бесплатный вариант: открытые веса на своей машине
#: (см. docs/llm_setup.md, там же — как заменить GigaChat/YandexGPT своей моделью).
AUTO_ORDER = ("api", "gigachat", "yandex", "local")

#: Синонимы имён провайдеров в LLM_PROVIDER: «openai»/«openrouter» и т.п. — это всё провайдер `api`.
_ALIASES = {
    "openai": "api",
    "openrouter": "api",
    "deepseek": "api",
    "groq": "api",
    "qwen": "api",
    "mistral": "api",
    "together": "api",
    "vllm": "api",
}


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
    return GigaChatLLM(
        s.gigachat_credentials,
        s.gigachat_scope,
        s.gigachat_model,
        s.gigachat_verify_ssl,
        s.gigachat_ca_bundle_file,
        s.gigachat_max_tokens,
    )


def _yandex(s: Settings) -> LLMProvider | None:
    if not (s.yandex_folder_id and (s.yandex_api_key or s.yandex_iam_token)):
        return None
    return YandexGPTLLM(
        api_key=s.yandex_api_key,
        folder_id=s.yandex_folder_id,
        model=s.yandex_model,
        model_uri=s.yandex_model_uri,
        max_tokens=s.yandex_max_tokens,
        iam_token=s.yandex_iam_token,
        reasoning_mode=s.yandex_reasoning_mode,
    )


def _api_enabled(s: Settings) -> bool:
    """Провайдер `api` включён, если задан ключ ИЛИ явно задан базовый URL.

    Безключевой вариант нужен для внутренних шлюзов и прокси (свой vLLM, корпоративный
    OpenAI-совместимый endpoint): там авторизации нет, но адрес задают руками — это и есть сигнал,
    что провайдером хотят пользоваться.
    """
    if not s.api_model:
        return False
    return bool(s.api_key or os.getenv("LLM_API_BASE_URL") or os.getenv("OPENAI_BASE_URL"))


def _api(s: Settings) -> LLMProvider | None:
    """Любая API-модель: ключ + базовый URL + имя модели (у последних двух есть умолчания)."""
    if not _api_enabled(s):
        return None
    return AnyApiLLM(
        model=s.api_model,
        base_url=s.api_base_url,
        api_key=s.api_key,
        max_tokens=s.api_max_tokens,
        json_mode=s.llm_json_mode,
    )


def _local(s: Settings) -> LLMProvider | None:
    if not s.local_model:
        return None
    return LocalLLM(
        model=s.local_model,
        base_url=s.local_base_url,
        api_key=s.local_api_key,
        max_tokens=s.local_max_tokens,
        json_mode=s.llm_json_mode,
    )


_FACTORIES: dict[str, Callable[[Settings], LLMProvider | None]] = {
    "api": _api,
    "gigachat": _gigachat,
    "yandex": _yandex,
    "local": _local,
}


def _requested(s: Settings) -> list[str]:
    """Разбирает LLM_PROVIDER в список имён (auto → порядок по умолчанию)."""
    names = [name.strip() for name in s.llm_provider.split(",") if name.strip()]
    if not names or "auto" in names:
        return list(AUTO_ORDER)
    # «openai» и «openrouter» — это один и тот же провайдер `api` со своими LLM_API_* значениями.
    return [_ALIASES.get(name, name) for name in names]


def _missing(s: Settings, name: str) -> list[str]:
    """Каких переменных не хватает провайдеру — это же подсказывает и /api/health."""
    if name == "api":
        # Ключ нужен облачным шлюзам; внутреннему прокси хватает явного LLM_API_BASE_URL.
        return [] if _api_enabled(s) else [
            "LLM_API_KEY (или OPENAI_API_KEY) либо LLM_API_BASE_URL для шлюза без ключа"
        ]
    if name == "gigachat":
        return [] if s.gigachat_credentials else ["GIGACHAT_CREDENTIALS"]
    if name == "yandex":
        missing: list[str] = []
        if not (s.yandex_api_key or s.yandex_iam_token):
            missing.append("YANDEX_API_KEY (или YANDEX_IAM_TOKEN)")
        if not s.yandex_folder_id:
            missing.append("YANDEX_FOLDER_ID")
        return missing
    if name == "local":
        # Локальная модель включается явно: без LOCAL_MODEL неизвестно, что за модель поднимать.
        return [] if s.local_model else ["LOCAL_MODEL (например, gpt-oss:20b или своя GGUF-модель)"]
    return []


def provider_status(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Кто настроен, а кого пропустили и почему: для /api/health и scripts/llm_check.py."""
    s = settings or get_settings()
    status: list[dict[str, Any]] = []
    for name in AUTO_ORDER:
        missing = _missing(s, name)
        status.append({"name": name, "configured": not missing, "missing": missing})
    status.append({"name": "mock", "configured": True, "missing": []})
    return status


def build_chain(settings: Settings | None = None) -> FallbackChain:
    """Цепочка по LLM_PROVIDER: выбранные облачные (у кого есть ключи) → Mock."""
    s = settings or get_settings()
    requested = _requested(s)
    if requested == ["mock"]:
        return FallbackChain([MockLLM()])

    order = [name for name in requested if name in _FACTORIES]
    unknown = [name for name in requested if name not in _FACTORIES and name != "mock"]
    if unknown:
        logger.warning("Неизвестный LLM_PROVIDER=%s — пропускаю", ",".join(unknown))
    # Второй облачный провайдер остаётся в цепочке: если ключи есть у обоих, это бесплатный
    # запас прочности, а без ключей он просто не попадёт в список ниже.
    order += [name for name in AUTO_ORDER if name not in order]

    providers: list[LLMProvider] = []
    for name in order:
        provider = _FACTORIES[name](s)
        if provider is not None:
            providers.append(provider)
        else:
            logger.info("Провайдер %s пропущен: не заданы %s", name, ", ".join(_missing(s, name)))
    providers.append(MockLLM())
    return FallbackChain(providers)


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    """Основная точка входа: провайдер (цепочка) по текущим настройкам."""
    return build_chain(settings)


def describe_llm(settings: Settings | None = None) -> dict[str, Any]:
    """Сводка для health-эндпоинтов: кто отвечает и чего не хватает для остальных."""
    return {
        "llm": build_chain(settings).name,
        "llm_ready": {item["name"]: item["configured"] for item in provider_status(settings)},
        "llm_missing": {
            item["name"]: item["missing"] for item in provider_status(settings) if item["missing"]
        },
    }
