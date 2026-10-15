"""Провайдер локальной модели: любой OpenAI-совместимый сервер на своей машине.

Один и тот же код закрывает Ollama, LM Studio, llama.cpp (`llama-server`), vLLM и любой
другой сервер с эндпоинтом `/chat/completions`. Так подключается бесплатный «российский»
контур — открытые веса под MIT/Apache: например GigaChat-20B-A3B (MIT) или
YandexGPT-5-Lite-8B-Instruct. Данные при этом не покидают машину: это важно, если в
диалогах есть персональные данные (152-ФЗ), — в облако уходит только маскированный текст.

Подробности и команды запуска: docs/llm_setup.md, раздел «Бесплатный вариант: локальная модель».
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from core.llm.base import BaseLLM, LLMError, LLMInvalidResponse, LLMTimeout

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # Ollama; LM Studio — :1234/v1, llama.cpp — :8080/v1


class LocalLLM(BaseLLM):
    """Локальная (или любая OpenAI-совместимая) модель: REST через stdlib, без SDK."""

    name = "local"

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        max_tokens: int = 800,
    ) -> None:
        self._model = model
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self._max_tokens = max_tokens

    @property
    def url(self) -> str:
        """Эндпоинт генерации. Сервер может отдавать и /v1, и без него — приводим к одному виду."""
        if self._base_url.endswith("/v1"):
            return f"{self._base_url}/chat/completions"
        return f"{self._base_url}/v1/chat/completions"

    def _payload(self, system: str, user: str, temperature: float) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }

    def _chat(self, system: str, user: str, temperature: float, timeout: float) -> str:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(self._payload(system, user, temperature)).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # HTTPError — подкласс URLError, ловим первым
            raise self._http_error(exc) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise LLMTimeout("local: timeout") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise LLMTimeout("local: timeout") from exc
            raise LLMError(
                f"local: сервер не отвечает ({exc.reason}). Запустите его, например: ollama serve"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponse("local: ответ не является JSON") from exc
        return self._extract_text(payload)

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        """OpenAI-формат: choices[0].message.content; Ollama собирает то же самое в /v1."""
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponse("local: неожиданная структура ответа") from exc
        if isinstance(content, list):  # часть серверов отдаёт контент частями
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not str(content).strip():
            raise LLMInvalidResponse("local: модель вернула пустой ответ")
        return str(content)

    def _http_error(self, exc: urllib.error.HTTPError) -> LLMError:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 — тело ошибки читается «по возможности»
            detail = ""
        if exc.code == 404:
            return LLMError(
                f"local: модель не найдена ({self._model}). Загрузите её: "
                f"`ollama pull <модель>` / `ollama list` — {detail}"
            )
        if exc.code == 401:
            return LLMError("local: 401 — проверьте LOCAL_API_KEY (для локальных серверов обычно пусто)")
        if exc.code == 429:
            return LLMError("local: 429 — сервер перегружен, повторите позже")
        return LLMError(f"local: {exc.code}: {detail}")
