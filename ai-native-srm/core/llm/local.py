"""Провайдер локальной модели: любой OpenAI-совместимый сервер на своей машине.

Один и тот же код закрывает Ollama, LM Studio, llama.cpp (`llama-server`), vLLM и любой
другой сервер с эндпоинтом `/chat/completions` (общая часть — core/llm/openai_compat.py).
Так подключается бесплатный «российский» контур — открытые веса под MIT/Apache: например
GigaChat-20B-A3B (MIT) или YandexGPT-5-Lite-8B-Instruct. Данные при этом не покидают машину:
это важно, если в диалогах есть персональные данные (152-ФЗ), — в облако уходит только
маскированный текст.

Подробности и команды запуска: docs/llm_setup.md, раздел «Бесплатный вариант: локальная модель».
"""
from __future__ import annotations

from core.llm.openai_compat import OpenAICompatibleLLM

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # Ollama; LM Studio — :1234/v1, llama.cpp — :8080/v1


class LocalLLM(OpenAICompatibleLLM):
    """Модель на своей машине: REST через stdlib, без SDK и без ключа."""

    name = "local"

    hint_401 = "local: 401 — проверьте LOCAL_API_KEY (для локальных серверов обычно пусто)"
    hint_429 = "local: 429 — сервер перегружен, повторите позже или уменьшите параллелизм"

    def __init__(
        self,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        max_tokens: int = 800,
        *,
        json_mode: bool = False,
    ) -> None:
        super().__init__(model, base_url or DEFAULT_BASE_URL, api_key, max_tokens, json_mode=json_mode)

    @property
    def hint_404(self) -> str:  # noqa: D102 — подсказка зависит от имени модели
        return (
            f"{self.name}: модель не найдена ({self.model}). Загрузите её: "
            "`ollama pull <модель>` / `ollama list`"
        )

    def _network_hint(self, exc: object) -> str:
        return (
            f"{self.name}: сервер не отвечает ({getattr(exc, 'reason', exc)}). "
            "Запустите его, например: ollama serve"
        )
