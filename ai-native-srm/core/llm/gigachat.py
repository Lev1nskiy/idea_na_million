"""Провайдер GigaChat (SDK `gigachat`, устанавливается отдельно: requirements-llm.txt).

Поддерживаются оба контракта SDK: старый `client.chat(payload)` (работает и в 0.1.x, и как
совместимый шим в 0.2.x) и новый ресурс `client.chat.create(...)`. Ответ читается в обоих
форматах, поэтому обновление SDK не ломает движок.
"""
from __future__ import annotations

import logging
from typing import Any

from core.llm.base import BaseLLM, LLMError, LLMTimeout

logger = logging.getLogger(__name__)

# Имена исключений SDK: сверяем по имени, чтобы не зависеть от версии пакета.
_AUTH = "AuthenticationError"
_FORBIDDEN = "ForbiddenError"
_RATE = "RateLimitError"
_NO_MODEL = "ModelNotSpecifiedError"
_TIMEOUTS = {"Timeout", "ConnectTimeout", "ReadTimeout", "TimeoutException"}


class GigaChatLLM(BaseLLM):
    """GigaChat через официальный Python-SDK: он сам держит OAuth-токен и обновляет его."""

    name = "gigachat"

    def __init__(
        self,
        credentials: str,
        scope: str = "GIGACHAT_API_PERS",
        model: str = "GigaChat-2",
        verify_ssl: bool = False,
        ca_bundle_file: str = "",
        max_tokens: int = 0,
    ) -> None:
        self._credentials = credentials
        self._scope = scope
        self._model = model
        self._verify_ssl = verify_ssl
        self._ca_bundle_file = ca_bundle_file
        self._max_tokens = max_tokens
        self._client: Any = None  # ленивая инициализация: клиент держит токен и соединения

    def _get_client(self, timeout: float) -> Any:
        if self._client is None:
            try:
                from gigachat import GigaChat
            except ImportError as exc:  # SDK не установлен
                raise LLMError(
                    "SDK gigachat не установлен: pip install -r requirements-llm.txt"
                ) from exc
            kwargs: dict[str, Any] = {
                "credentials": self._credentials,
                "scope": self._scope,
                "model": self._model,
                "verify_ssl_certs": self._verify_ssl,
                # Без явного таймаута SDK ждёт 30 с; бюджет времени задаёт цепочка fallback,
                # поэтому просим SDK не пересиживать его.
                "timeout": max(timeout, 1.0),
            }
            if self._ca_bundle_file:
                kwargs["ca_bundle_file"] = self._ca_bundle_file
            try:
                self._client = GigaChat(**kwargs)
            except TypeError as exc:
                # Старые сборки SDK не знают части параметров — хватит ключа, scope и модели.
                logger.warning("gigachat: SDK не принял часть параметров (%s), упрощаю запрос", exc)
                self._client = GigaChat(
                    credentials=self._credentials, scope=self._scope, model=self._model
                )
        return self._client

    def _chat(self, system: str, user: str, temperature: float, timeout: float) -> str:
        client = self._get_client(timeout)
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # GigaChat не принимает temperature = 0: минимальное осмысленное значение — 0.01
            "temperature": max(temperature, 0.01),
        }
        if self._model:
            payload["model"] = self._model
        if self._max_tokens > 0:
            payload["max_tokens"] = self._max_tokens

        try:
            response = client.chat(payload)
        except (TypeError, AttributeError):
            # SDK 0.2.x: client.chat — ресурс, а не метод (client.chat.create(payload)).
            try:
                response = client.chat.create(payload)
            except Exception as exc:  # noqa: BLE001 — любую ошибку SDK отдаём как LLMError
                raise self._wrap(exc) from exc
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc) from exc
        return self._extract_text(response)

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Текст ответа: старый контракт `choices[0].message.content`, новый — `messages[*]`."""
        choices = getattr(response, "choices", None)
        if choices:
            content = getattr(getattr(choices[0], "message", None), "content", None)
            if isinstance(content, str) and content.strip():
                return content
        messages = getattr(response, "messages", None)
        if messages:
            content = getattr(messages[0], "content", None)
            if isinstance(content, str) and content.strip():
                return content
            parts = content if isinstance(content, (list, tuple)) else []
            for part in parts:
                text = getattr(part, "text", None)
                if text:
                    return str(text)
        raise LLMError("gigachat: в ответе модели нет текста")

    @staticmethod
    def _wrap(exc: Exception) -> LLMError:
        """Ошибку SDK превращаем в понятную подсказку по настройке (см. docs/llm_setup.md)."""
        name = type(exc).__name__
        if name == _AUTH:
            return LLMError(
                "gigachat: 401 — проверьте GIGACHAT_CREDENTIALS (ключ авторизации из личного кабинета)"
            )
        if name == _FORBIDDEN:
            return LLMError(
                "gigachat: 403 — ключу недоступна модель: сверьте GIGACHAT_SCOPE и GIGACHAT_MODEL"
            )
        if name == _RATE:
            return LLMError("gigachat: 429 — исчерпана квота запросов, повторите позже")
        if name == _NO_MODEL:
            return LLMError("gigachat: не задана модель — укажите GIGACHAT_MODEL (например, GigaChat-2)")
        if name in _TIMEOUTS or isinstance(exc, TimeoutError):
            return LLMTimeout("gigachat: timeout")
        return LLMError(f"gigachat: {name}: {str(exc)[:160]}")
