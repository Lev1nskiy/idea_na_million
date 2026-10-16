"""Провайдер «любая API-модель»: OpenAI-совместимый чат из облака по переменным окружения.

Смысл провайдера — не привязывать проект к конкретному вендору. Меняется не код, а три строки в
`.env`, поэтому вместо «или GigaChat, или YandexGPT» можно поставить любую API-модель:

    OPENAI / Azure-совместимые шлюзы   LLM_API_BASE_URL=https://api.openai.com/v1
    OpenRouter (сотни моделей)         LLM_API_BASE_URL=https://openrouter.ai/api/v1
    DeepSeek                           LLM_API_BASE_URL=https://api.deepseek.com/v1
    Groq (быстрый инференс)            LLM_API_BASE_URL=https://api.groq.com/openai/v1
    Qwen / DashScope                   LLM_API_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    Mistral, Together, Cerebras, vLLM …  →  любой эндпоинт с /chat/completions

Провайдер стоит первым в цепочке `auto` (`AUTO_ORDER`): если ключ задан — отвечает он,
а GigaChat/YandexGPT/локальная модель остаются запасом. Полностью заменить их можно так:
`LLM_PROVIDER=api,mock` (`mock` замыкает цепочку всегда, см. core/llm/__init__.py).

Данные: как и у любого облачного провайдера, в сеть уходит только маскированный текст
(core/masking.py), персональные данные остаются в контуре CRM. Если по 152-ФЗ модель обязана
быть в РФ — ставьте на первое место GigaChat/YandexGPT или локальные веса
(`LLM_PROVIDER=gigachat,api` и `LLM_PROVIDER=local`), см. docs/llm_setup.md.
"""
from __future__ import annotations

from core.llm.openai_compat import OpenAICompatibleLLM

#: По умолчанию — публичный API OpenAI: совместимые шлюзы меняются одной переменной.
DEFAULT_BASE_URL = "https://api.openai.com/v1"
#: Частая модель у шлюзов с открытыми весами; у OpenAI/Anthropic-прокси её надо задать явно.
DEFAULT_MODEL = "gpt-4o-mini"


class AnyApiLLM(OpenAICompatibleLLM):
    """Любая модель с OpenAI-совместимым API: ключ + базовый URL + имя модели."""

    name = "api"

    hint_401 = "401 — ключ не принят. Проверьте LLM_API_KEY (или OPENAI_API_KEY) у провайдера"
    hint_402 = "402 — на счету провайдера нет средств: пополните баланс или смените модель"
    hint_403 = "403 — у ключа нет доступа к этой модели: проверьте тариф и LLM_API_MODEL"
    hint_429 = "429 — лимит запросов или квота: подождите, смените модель или тариф"
    hint_network = "провайдер не отвечает: проверьте LLM_API_BASE_URL, сеть и прокси"

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "",
        max_tokens: int = 800,
        *,
        json_mode: bool = False,
    ) -> None:
        super().__init__(model, base_url or DEFAULT_BASE_URL, api_key, max_tokens, json_mode=json_mode)

    @property
    def hint_404(self) -> str:  # noqa: D102 — зависит от имени модели, поэтому вычисляется
        return (
            f"404 — модель «{self.model}» недоступна по адресу {self.url}: "
            "проверьте LLM_API_MODEL и LLM_API_BASE_URL (у шлюзов модель указывают как vendor/model)"
        )
