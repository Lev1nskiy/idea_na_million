"""Провайдер YandexGPT (REST Yandex Foundation Models, без внешних зависимостей).

Модели 4-го поколения (`yandexgpt`, `yandexgpt-lite`) выведены 28.08.2025 и отвечают 400 —
рабочий идентификатор задаётся в `YANDEX_MODEL` (по умолчанию `yandexgpt-5-lite`), а полный
`gpt://…` можно переопределить через `YANDEX_MODEL_URI` (например, для дообученной модели).
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from core.llm.base import BaseLLM, LLMError, LLMInvalidResponse, LLMTimeout

_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
# Режим рассуждений умеют только модели 5-го поколения Pro/Lite; у остальных поля нет вовсе.
_REASONING_MODELS = {"yandexgpt-5-pro", "yandexgpt-5-lite"}


class YandexGPTLLM(BaseLLM):
    """YandexGPT через REST: один POST на запрос, stdlib, без SDK и лишних зависимостей."""

    name = "yandex"

    def __init__(
        self,
        api_key: str,
        folder_id: str,
        model: str = "yandexgpt-5-lite",
        model_uri: str = "",
        max_tokens: int = 800,
        iam_token: str = "",
        reasoning_mode: str = "",
    ) -> None:
        self._api_key = api_key
        self._iam_token = iam_token
        self._folder_id = folder_id
        self._model = model
        self._model_uri = model_uri
        self._max_tokens = max_tokens
        # Пусто и модель не из 5-го поколения — поле не отправляем: старые модели его не знают.
        # Для 5-pro/5-lite по умолчанию выключаем рассуждения: клиенту нужен короткий ответ,
        # а не цепочка размышлений (токены и секунды).
        self._reasoning_mode = reasoning_mode or (
            "disabled" if model.split("/")[0] in _REASONING_MODELS else ""
        )

    @property
    def model_uri(self) -> str:
        """`gpt://<каталог>/<модель>`; полный URI из настроек имеет приоритет."""
        if self._model_uri:
            return self._model_uri
        return f"gpt://{self._folder_id}/{self._model}"

    def _auth_header(self) -> str:
        """IAM-токен (если задан) уходит как Bearer, иначе — статический API-ключ сервисного аккаунта."""
        if self._iam_token:
            return f"Bearer {self._iam_token}"
        return f"Api-Key {self._api_key}"

    def _payload(self, system: str, user: str, temperature: float) -> dict[str, Any]:
        options: dict[str, Any] = {
            "stream": False,
            "temperature": temperature,
            "maxTokens": str(self._max_tokens),
        }
        if self._reasoning_mode:
            options["reasoningOptions"] = {"mode": self._reasoning_mode.upper()}
        return {
            "modelUri": self.model_uri,
            "completionOptions": options,
            "messages": [
                {"role": "system", "text": system},
                {"role": "user", "text": user},
            ],
        }

    def _chat(self, system: str, user: str, temperature: float, timeout: float) -> str:
        request = urllib.request.Request(
            _URL,
            data=json.dumps(self._payload(system, user, temperature)).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": self._auth_header(),
                "x-folder-id": self._folder_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # HTTPError — подкласс URLError, ловим первым
            raise self._http_error(exc) from exc
        except (socket.timeout, TimeoutError) as exc:
            raise LLMTimeout("yandex: timeout") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise LLMTimeout("yandex: timeout") from exc
            raise LLMError(f"yandex: нет соединения ({exc.reason})") from exc
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponse("yandex: ответ не является JSON") from exc
        try:
            return str(payload["result"]["alternatives"][0]["message"]["text"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMInvalidResponse("yandex: неожиданная структура ответа") from exc

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> LLMError:
        """Код + тело ошибки + подсказка по настройке: чаще всего это ключ, роль или каталог."""
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001 — тело ошибки читается «по возможности»
            detail = ""
        code = exc.code
        if code == 401:
            return LLMError("yandex: 401 — проверьте YANDEX_API_KEY (или YANDEX_IAM_TOKEN)")
        if code == 403:
            return LLMError("yandex: 403 — у сервисного аккаунта нет роли ai.languageModels.user")
        if code == 400:
            return LLMError(f"yandex: 400 — проверьте YANDEX_MODEL, YANDEX_FOLDER_ID: {detail}")
        if code == 429:
            return LLMError("yandex: 429 — исчерпана квота запросов, повторите позже")
        return LLMError(f"yandex: {code}: {detail}")
