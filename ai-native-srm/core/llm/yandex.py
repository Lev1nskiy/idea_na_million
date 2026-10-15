"""Провайдер YandexGPT (REST, без внешних зависимостей)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from core.llm.base import BaseLLM, LLMError, LLMTimeout

_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


class YandexGPTLLM(BaseLLM):
    name = "yandex"

    def __init__(self, api_key: str, folder_id: str, model: str = "yandexgpt-lite") -> None:
        self._api_key = api_key
        self._folder_id = folder_id
        self._model = model

    def _chat(self, system: str, user: str, temperature: float, timeout: float) -> str:
        body = {
            "modelUri": f"gpt://{self._folder_id}/{self._model}/latest",
            "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": "800"},
            "messages": [
                {"role": "system", "text": system},
                {"role": "user", "text": user},
            ],
        }
        request = urllib.request.Request(
            _URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Api-Key {self._api_key}",
                "x-folder-id": self._folder_id,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return str(payload["result"]["alternatives"][0]["message"]["text"])
        except TimeoutError as exc:
            raise LLMTimeout("yandex: timeout") from exc
        except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as exc:
            raise LLMError(f"yandex: {type(exc).__name__}") from exc
