"""Провайдер GigaChat (через SDK `gigachat`, pip install gigachat)."""
from __future__ import annotations

from typing import Any

from core.llm.base import BaseLLM, LLMError


class GigaChatLLM(BaseLLM):
    name = "gigachat"

    def __init__(
        self,
        credentials: str,
        scope: str = "GIGACHAT_API_PERS",
        model: str = "GigaChat",
        verify_ssl: bool = False,
    ) -> None:
        self._credentials = credentials
        self._scope = scope
        self._model = model
        self._verify_ssl = verify_ssl
        self._client: Any = None  # ленивая инициализация: SDK держит токен и обновляет его сам

    def _get_client(self, timeout: float) -> Any:
        if self._client is None:
            try:
                from gigachat import GigaChat
            except ImportError as exc:  # SDK не установлен
                raise LLMError("SDK gigachat не установлен: pip install gigachat") from exc
            self._client = GigaChat(
                credentials=self._credentials,
                scope=self._scope,
                model=self._model,
                verify_ssl_certs=self._verify_ssl,
                timeout=timeout,
            )
        return self._client

    def _chat(self, system: str, user: str, temperature: float, timeout: float) -> str:
        client = self._get_client(timeout)
        try:
            response = client.chat(
                {
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    # минимальная температура ≈ 0 для детерминированности
                    "temperature": max(temperature, 0.01),
                }
            )
            return str(response.choices[0].message.content)
        except LLMError:
            raise
        except Exception as exc:
            raise LLMError(f"gigachat: {type(exc).__name__}") from exc
