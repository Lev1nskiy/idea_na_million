"""Общий REST-провайдер для любой модели с OpenAI-совместимым чатом (`/chat/completions`).

Один и тот же код закрывает два очень разных случая, потому что протокол у них один:

* **любой облачный API** — OpenAI, OpenRouter, DeepSeek, Groq, Qwen/DashScope, Mistral, Together,
  Cerebras и прочие шлюзы: `Authorization: Bearer <ключ>`, модель по имени;
* **локальный сервер** — Ollama, LM Studio, llama.cpp (`llama-server`), vLLM: тот же JSON,
  только без ключа и на `127.0.0.1`.

Отличия вынесены в наследников: базовый URL по умолчанию, подсказки к ошибкам и то, нужен ли ключ
(`core/llm/api.py` — боевая модель, `core/llm/local.py` — свои веса). Провайдер ходит через stdlib
`urllib`, SDK не нужен: провайдер подключается переменными окружения, без правки кода.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from core.llm.base import (
    DEFAULT_TIMEOUT_S,
    BaseLLM,
    LLMError,
    LLMInvalidResponse,
    LLMTimeout,
    extract_json,
    json_instruction,
)


#: Подпись клиента: по ней провайдер видит не анонимный скрипт, а конкретное приложение.
USER_AGENT = "LOS-CRM/1.0 (+https://github.com/Lev1nskiy/idea_na_million)"

#: Коды Cloudflare в теле ответа: защита провайдера отбросила запрос (403) или притормозила его (429).
_CLOUDFLARE_BANS = ("error code: 1010", "error code: 1012", "error code: 1015", "error code: 1020")


class OpenAICompatibleLLM(BaseLLM):
    """Чат-модель по OpenAI-протоколу: `POST {base_url}/chat/completions`."""

    name = "openai-compatible"

    #: Подсказки к HTTP-статусам — их переопределяют наследники под свою среду.
    hint_401 = "401 — неверный ключ"
    hint_402 = "402 — закончились средства на счёте провайдера"
    hint_403 = "403 — у ключа нет доступа к этой модели"
    hint_404 = "404 — модель не найдена"
    hint_429 = "429 — исчерпан лимит запросов или квота"
    hint_network = "сервер не отвечает"

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str = "",
        max_tokens: int = 800,
        *,
        json_mode: bool = False,
    ) -> None:
        self._model = model
        self._base_url = (base_url or "").rstrip("/")
        self._api_key = api_key
        self._max_tokens = max_tokens
        # JSON-режим (`response_format`) поддерживают не все шлюзы, поэтому он выключен по умолчанию:
        # без него JSON всё равно достаётся из текста (extract_json), просто чуть менее строго.
        self._json_mode = json_mode

    @property
    def model(self) -> str:
        return self._model

    @property
    def url(self) -> str:
        """Эндпоинт генерации: `{base}/chat/completions`.

        Базу задают по-разному, поэтому приводим к одному виду: у OpenAI/DeepSeek/Groq путь
        заканчивается на `/v1`, у Gemini — на `/v1beta/openai`, у Cloudflare — на
        `/accounts/<id>/ai/v1`. Дан голый хост — дописываем привычный `/v1`.
        """
        if self._base_url.endswith(("/v1", "/openai")):
            return f"{self._base_url}/chat/completions"
        return f"{self._base_url}/v1/chat/completions"

    def _payload(self, system: str, user: str, temperature: float, force_json: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        if force_json:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """JSON-ответ: в JSON-режиме он приходит структурой, иначе достаём объект из текста."""
        if not self._json_mode:
            return super().complete_json(system, user, schema, timeout=timeout, temperature=temperature)
        raw = self._chat(system + json_instruction(schema), user, temperature, timeout, force_json=True)
        return extract_json(raw)

    def _chat(
        self,
        system: str,
        user: str,
        temperature: float,
        timeout: float,
        *,
        force_json: bool = False,
    ) -> str:
        headers = {
            "Content-Type": "application/json",
            # Свой User-Agent обязателен: облачные API стоят за Cloudflare, а тот режет запросы
            # со стандартной подписью stdlib (`Python-urllib/3.x`) — ответ 403 «error code: 1010».
            # Клиент без имени выглядит для защиты как бот, поэтому представляемся честно.
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(self._payload(system, user, temperature, force_json)).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # HTTPError — подкласс URLError, ловим первым
            raise self._http_error(exc) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise LLMTimeout(f"{self.name}: timeout") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise LLMTimeout(f"{self.name}: timeout") from exc
            raise LLMError(self._network_hint(exc)) from exc
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponse(f"{self.name}: ответ не является JSON") from exc
        return self._extract_text(payload)

    def _network_hint(self, exc: urllib.error.URLError) -> str:
        return f"{self.name}: {self.hint_network} ({exc.reason})"

    def _extract_text(self, payload: dict[str, Any]) -> str:
        """OpenAI-формат: `choices[0].message.content`."""
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponse(f"{self.name}: неожиданная структура ответа") from exc
        if isinstance(content, list):  # часть серверов отдаёт контент частями
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        if not str(content).strip():
            raise LLMInvalidResponse(f"{self.name}: модель вернула пустой ответ")
        return str(content)

    def _http_error(self, exc: urllib.error.HTTPError) -> LLMError:
        detail = self._body(exc)
        if any(marker in detail.lower() for marker in _CLOUDFLARE_BANS):
            # 403/429 от Cloudflare — не «нет доступа к модели»: отбросил сам защитный слой.
            return LLMError(
                f"{self.name}: провайдер отклонил запрос на уровне защиты Cloudflare ({detail[:120]}). "
                "Проверьте, что базовый URL — из документации провайдера, и что клиент отправляет "
                "нормальный User-Agent (в этой версии он уже отправляется)"
            )
        hints = {
            401: self.hint_401,
            402: self.hint_402,
            403: self.hint_403,
            404: self.hint_404,
            429: self.hint_429,
        }
        hint = hints.get(exc.code, f"{exc.code}")
        tail = f" — {detail}" if detail else ""
        return LLMError(f"{self.name}: {hint}{tail}")

    @staticmethod
    def _body(exc: urllib.error.HTTPError) -> str:
        """Тело ошибки читается «по возможности»: провайдеры кладут туда причину отказа."""
        try:
            return exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 — диагностика не должна ломать обработку ошибки
            return ""
