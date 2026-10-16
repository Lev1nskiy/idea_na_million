"""LLM-провайдеры: любая API-модель, GigaChat, YandexGPT и локальные веса — без ключей и без сети.

Тесты проверяют то, что обычно ломается при подключении: какой payload уходит в API,
как читается ответ (оба контракта SDK и OpenAI-совместимый чат), как ошибка провайдера
превращается в подсказку и что цепочка fallback доходит до mock. Сеть подменяется, ключи фальшивые.
"""
from __future__ import annotations

import json
import socket
import sys
import types
import urllib.error
import urllib.request
from typing import Any

import pytest

from core.config import get_settings
from core.llm import (
    FallbackChain,
    LLMError,
    MockLLM,
    build_chain,
    describe_llm,
    provider_status,
)
from core.llm.api import AnyApiLLM
from core.llm.base import LLMTimeout, default_timeout
from core.llm.gigachat import GigaChatLLM
from core.llm.local import LocalLLM
from core.llm.yandex import YandexGPTLLM

# ---------------------------------------------------------------- GigaChat

class _FakeResponse:
    """Ответ в форме старого контракта: choices[0].message.content."""

    def __init__(self, text: str) -> None:
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=text))]


class _FakeSdkError(Exception):
    """Исключение «SDK» с настраиваемым именем класса (SDK различает ошибки по типам)."""


def _sdk_error(name: str, message: str = "boom") -> Exception:
    return type(name, (_FakeSdkError,), {})(message)


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, chat_callable: Any) -> dict[str, Any]:
    """Подменяет пакет `gigachat`: SDK не обязателен для тестов ядра."""
    calls: dict[str, Any] = {}

    class FakeGigaChat:
        def __init__(self, **kwargs: Any) -> None:
            calls["init"] = kwargs
            self.chat = chat_callable

    module = types.ModuleType("gigachat")
    module.GigaChat = FakeGigaChat  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gigachat", module)
    return calls


def test_gigachat_sends_system_and_user_message(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def chat(payload: dict[str, Any]) -> _FakeResponse:
        sent.update(payload)
        return _FakeResponse("Скидка 8 % оформлена.")

    calls = _install_fake_sdk(monkeypatch, chat)
    llm = GigaChatLLM("key", "GIGACHAT_API_PERS", "GigaChat-2", verify_ssl=False, max_tokens=200)

    assert llm.complete_text("Ты менеджер", "Скидка?", timeout=2.0) == "Скидка 8 % оформлена."
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["messages"][0]["content"] == "Ты менеджер"
    assert sent["model"] == "GigaChat-2" and sent["max_tokens"] == 200
    assert sent["temperature"] >= 0.01  # GigaChat не принимает 0
    # timeout и режим TLS доезжают до конструктора SDK
    assert calls["init"]["timeout"] == 2.0 and calls["init"]["verify_ssl_certs"] is False
    assert calls["init"]["scope"] == "GIGACHAT_API_PERS"


def test_gigachat_accepts_new_sdk_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK 0.2.x: client.chat — ресурс с методом create, текст лежит в messages[*].content[*].text."""

    def create(payload: dict[str, Any]) -> Any:
        part = types.SimpleNamespace(text=f"Ответ на {len(payload['messages'])} сообщения")
        message = types.SimpleNamespace(content=[part])
        return types.SimpleNamespace(messages=[message])

    _install_fake_sdk(monkeypatch, types.SimpleNamespace(create=create))
    llm = GigaChatLLM("key")

    assert llm.complete_text("s", "u", timeout=2.0) == "Ответ на 2 сообщения"


@pytest.mark.parametrize(
    ("error_name", "hint"),
    [
        ("AuthenticationError", "GIGACHAT_CREDENTIALS"),
        ("ForbiddenError", "GIGACHAT_SCOPE"),
        ("RateLimitError", "квота"),
        ("ModelNotSpecifiedError", "GIGACHAT_MODEL"),
    ],
)
def test_gigachat_errors_give_setup_hints(
    monkeypatch: pytest.MonkeyPatch, error_name: str, hint: str
) -> None:
    def chat(payload: dict[str, Any]) -> Any:
        raise _sdk_error(error_name)

    _install_fake_sdk(monkeypatch, chat)
    llm = GigaChatLLM("key")

    with pytest.raises(LLMError) as exc_info:
        llm.complete_text("s", "u", timeout=2.0)
    assert hint in str(exc_info.value)


def test_gigachat_timeout_is_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def chat(payload: dict[str, Any]) -> Any:
        raise _sdk_error("ReadTimeout")

    _install_fake_sdk(monkeypatch, chat)
    with pytest.raises(LLMTimeout):
        GigaChatLLM("key").complete_text("s", "u", timeout=2.0)


def test_gigachat_without_sdk_explains_how_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "gigachat", None)  # import gigachat → ImportError
    with pytest.raises(LLMError) as exc_info:
        GigaChatLLM("key").complete_text("s", "u", timeout=2.0)
    assert "requirements-llm.txt" in str(exc_info.value)


# ---------------------------------------------------------------- YandexGPT


class _FakeHTTP:
    """Подмена urllib: перехватывает запрос, отдаёт заранее заданный ответ."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None, error: Exception | None = None):
        self.status = status
        self.body = body if body is not None else {
            "result": {"alternatives": [{"message": {"role": "assistant", "text": "Ответ YandexGPT"}}]}
        }
        self.error = error
        self.request: urllib.request.Request | None = None
        self.timeout: float | None = None

    def __call__(self, request: urllib.request.Request, timeout: float | None = None) -> Any:
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error

        class _Ctx:
            def __enter__(inner) -> Any:  # noqa: N805
                return types.SimpleNamespace(read=lambda: json.dumps(self.body).encode())

            def __exit__(inner, *args: Any) -> bool:  # noqa: N805
                return False

        return _Ctx()


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, fake: _FakeHTTP) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", fake)


def _sent_payload(fake: _FakeHTTP) -> dict[str, Any]:
    assert fake.request is not None and fake.request.data is not None
    return json.loads(fake.request.data.decode())


def test_yandex_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP()
    _patch_urlopen(monkeypatch, fake)
    llm = YandexGPTLLM(api_key="api-key", folder_id="b1gfolder", model="yandexgpt-5-lite")

    assert llm.complete_text("Ты менеджер", "Скидка?", timeout=4.0) == "Ответ YandexGPT"

    assert fake.request is not None
    assert fake.request.full_url == "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {k.lower(): v for k, v in fake.request.headers.items()}
    assert headers["authorization"] == "Api-Key api-key"
    assert headers["x-folder-id"] == "b1gfolder"
    assert fake.timeout == 4.0  # бюджет времени из цепочки доезжает до urllib

    payload = _sent_payload(fake)
    assert payload["modelUri"] == "gpt://b1gfolder/yandexgpt-5-lite"
    assert payload["messages"][0]["role"] == "system" and payload["messages"][1]["role"] == "user"
    assert payload["completionOptions"]["stream"] is False
    assert payload["completionOptions"]["maxTokens"] == "800"


def test_yandex_reasoning_mode_only_for_fifth_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP()
    _patch_urlopen(monkeypatch, fake)

    YandexGPTLLM("k", "f", model="yandexgpt-5-pro").complete_text("s", "u", timeout=3.0)
    assert _sent_payload(fake)["completionOptions"]["reasoningOptions"] == {"mode": "DISABLED"}

    YandexGPTLLM("k", "f", model="yandexgpt-5.1").complete_text("s", "u", timeout=3.0)
    assert "reasoningOptions" not in _sent_payload(fake)["completionOptions"]

    YandexGPTLLM("k", "f", model="yandexgpt-5-lite", reasoning_mode="enabled").complete_text("s", "u", timeout=3.0)
    assert _sent_payload(fake)["completionOptions"]["reasoningOptions"] == {"mode": "ENABLED"}


def test_yandex_supports_iam_token_and_custom_model_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP()
    _patch_urlopen(monkeypatch, fake)
    llm = YandexGPTLLM(
        api_key="", folder_id="f", iam_token="t1.abc", model="yandexgpt-lite/latest",
        model_uri="gpt://f/custom-model",
    )

    llm.complete_text("s", "u", timeout=3.0)
    assert fake.request is not None
    assert {k.lower(): v for k, v in fake.request.headers.items()}["authorization"] == "Bearer t1.abc"
    assert _sent_payload(fake)["modelUri"] == "gpt://f/custom-model"


def test_yandex_legacy_model_keeps_version_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP()
    _patch_urlopen(monkeypatch, fake)
    YandexGPTLLM("k", "f", model="yandexgpt-lite/latest").complete_text("s", "u", timeout=3.0)
    assert _sent_payload(fake)["modelUri"] == "gpt://f/yandexgpt-lite/latest"


@pytest.mark.parametrize(
    ("code", "hint"),
    [(401, "YANDEX_API_KEY"), (403, "ai.languageModels.user"), (400, "YANDEX_MODEL"), (429, "квота")],
)
def test_yandex_http_errors_give_setup_hints(monkeypatch: pytest.MonkeyPatch, code: int, hint: str) -> None:
    error = urllib.error.HTTPError("url", code, "err", {}, None)  # type: ignore[arg-type]
    _patch_urlopen(monkeypatch, _FakeHTTP(error=error))

    with pytest.raises(LLMError) as exc_info:
        YandexGPTLLM("k", "f").complete_text("s", "u", timeout=3.0)
    assert hint in str(exc_info.value) and str(code) in str(exc_info.value)


def test_yandex_timeout_and_broken_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, _FakeHTTP(error=urllib.error.URLError(TimeoutError("timed out"))))
    with pytest.raises(LLMTimeout):
        YandexGPTLLM("k", "f").complete_text("s", "u", timeout=3.0)

    _patch_urlopen(monkeypatch, _FakeHTTP(body={"error": "no result here"}))
    with pytest.raises(LLMError):
        YandexGPTLLM("k", "f").complete_text("s", "u", timeout=3.0)


# ---------------------------------------------------------------- любая API-модель


def test_api_uses_any_openai_compatible_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ + базовый URL + модель — этого достаточно, чтобы отвечала любая API-модель."""
    fake = _FakeHTTP(body={"choices": [{"message": {"content": "Ответ шлюза"}}], "model": "deepseek-chat"})
    _patch_urlopen(monkeypatch, fake)
    llm = AnyApiLLM(
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-any",
        max_tokens=400,
    )

    assert llm.complete_text("Ты менеджер", "Скидка?", timeout=5.0) == "Ответ шлюза"
    assert fake.request is not None
    assert fake.request.full_url == "https://api.deepseek.com/v1/chat/completions"
    assert {k.lower(): v for k, v in fake.request.headers.items()}["authorization"] == "Bearer sk-any"
    payload = _sent_payload(fake)
    assert payload["model"] == "deepseek-chat" and payload["max_tokens"] == 400
    # JSON-режим выключен по умолчанию: шлюзы, которые не знают response_format, не ломаются
    assert "response_format" not in payload


def test_api_json_mode_asks_provider_for_strict_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP(body={"choices": [{"message": {"content": '{"intent": "faq"}'}}]})
    _patch_urlopen(monkeypatch, fake)
    llm = AnyApiLLM(model="gpt-4o-mini", base_url="https://api.openai.com/v1", api_key="k", json_mode=True)

    assert llm.complete_json("s", "u", {"intent": "строка"}, timeout=5.0) == {"intent": "faq"}
    assert _sent_payload(fake)["response_format"] == {"type": "json_object"}


def test_api_json_without_json_mode_is_extracted_from_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шлюзы без response_format отвечают текстом — JSON всё равно достаём (```-ограждения тоже)."""
    fake = _FakeHTTP(body={"choices": [{"message": {"content": 'Категория: B\n```json\n{"intent": "faq", "c": "B"}\n```'}}]})
    _patch_urlopen(monkeypatch, fake)
    llm = AnyApiLLM(model="qwen-plus", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", api_key="k")

    assert llm.complete_json("s", "u", None, timeout=5.0) == {"intent": "faq", "c": "B"}


@pytest.mark.parametrize(
    ("code", "hint"),
    [
        (401, "LLM_API_KEY"),
        (402, "средств"),
        (403, "LLM_API_MODEL"),
        (429, "лимит"),
    ],
)
def test_api_errors_give_setup_hints(monkeypatch: pytest.MonkeyPatch, code: int, hint: str) -> None:
    _patch_urlopen(monkeypatch, _FakeHTTP(
        error=urllib.error.HTTPError("url", code, "err", {}, None)  # type: ignore[arg-type]
    ))
    with pytest.raises(LLMError) as exc_info:
        AnyApiLLM("gpt-4o-mini", "https://api.openai.com/v1", "k").complete_text("s", "u", timeout=2.0)
    assert hint in str(exc_info.value) and str(code) in str(exc_info.value)


def test_api_404_names_model_and_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """У шлюзов модель указывают как vendor/model — ошибка должна подсказывать, где смотреть."""
    _patch_urlopen(monkeypatch, _FakeHTTP(
        error=urllib.error.HTTPError("url", 404, "not found", {}, None)  # type: ignore[arg-type]
    ))
    with pytest.raises(LLMError) as exc_info:
        AnyApiLLM("no-such-model", "https://openrouter.ai/api/v1", "k").complete_text("s", "u", timeout=2.0)
    message = str(exc_info.value)
    assert "no-such-model" in message and "openrouter.ai" in message and "LLM_API_MODEL" in message


def test_api_network_error_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, _FakeHTTP(error=urllib.error.URLError(ConnectionRefusedError(111, "refused"))))
    with pytest.raises(LLMError) as exc_info:
        AnyApiLLM("m", "https://gw.example/v1", "k").complete_text("s", "u", timeout=2.0)
    assert "LLM_API_BASE_URL" in str(exc_info.value)

    _patch_urlopen(monkeypatch, _FakeHTTP(error=socket.timeout("slow")))
    with pytest.raises(LLMTimeout):
        AnyApiLLM("m", "https://gw.example/v1", "k").complete_text("s", "u", timeout=1.0)


def test_api_provider_two_providers_same_protocol_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Облако и локальный сервер — один код, но разные адреса: проверяем, что не перепутаны."""
    fake = _FakeHTTP(body={"choices": [{"message": {"content": "облако"}}]})
    _patch_urlopen(monkeypatch, fake)
    AnyApiLLM("m", "https://api.openai.com/v1", "k").complete_text("s", "u", timeout=3.0)
    cloud_url = fake.request.full_url if fake.request else ""

    local = _FakeHTTP(body={"choices": [{"message": {"content": "локально"}}]})
    _patch_urlopen(monkeypatch, local)
    LocalLLM("m").complete_text("s", "u", timeout=3.0)
    assert cloud_url == "https://api.openai.com/v1/chat/completions"
    assert local.request is not None and local.request.full_url == "http://127.0.0.1:11434/v1/chat/completions"


# ---------------------------------------------------------------- локальная модель


def _openai_body(text: str = "Локальный ответ") -> dict[str, Any]:
    """Ответ в формате OpenAI (его отдают Ollama /v1, LM Studio, llama.cpp, vLLM)."""
    return {"choices": [{"message": {"role": "assistant", "content": text}, "index": 0}], "model": "local"}


def test_local_openai_payload_and_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama/LM Studio/llama.cpp/vLLM говорят на одном OpenAI-совместимом протоколе."""
    fake = _FakeHTTP(body=_openai_body())
    _patch_urlopen(monkeypatch, fake)
    llm = LocalLLM(model="gigachat-20b", base_url="http://127.0.0.1:11434/v1", max_tokens=512)

    assert llm.complete_text("Ты менеджер", "Скидка?", timeout=5.0) == "Локальный ответ"
    assert fake.request is not None
    assert fake.request.full_url == "http://127.0.0.1:11434/v1/chat/completions"
    payload = _sent_payload(fake)
    assert payload["model"] == "gigachat-20b" and payload["stream"] is False
    assert payload["max_tokens"] == 512
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    # ключа нет — заголовок авторизации не отправляем (локальные серверы его не требуют)
    assert "authorization" not in {k.lower() for k in fake.request.headers}


def test_local_base_url_without_v1_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP(body=_openai_body())
    _patch_urlopen(monkeypatch, fake)
    LocalLLM("model", base_url="http://127.0.0.1:8080").complete_text("s", "u", timeout=3.0)
    assert fake.request is not None
    assert fake.request.full_url == "http://127.0.0.1:8080/v1/chat/completions"


def test_local_api_key_for_gateways(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHTTP(body=_openai_body())
    _patch_urlopen(monkeypatch, fake)
    LocalLLM("model", api_key="sk-local").complete_text("s", "u", timeout=3.0)
    assert fake.request is not None
    assert {k.lower(): v for k, v in fake.request.headers.items()}["authorization"] == "Bearer sk-local"


def test_local_errors_are_actionable(monkeypatch: pytest.MonkeyPatch) -> None:
    # сервер не запущен — подсказываем команду
    _patch_urlopen(monkeypatch, _FakeHTTP(error=urllib.error.URLError(ConnectionRefusedError(111, "refused"))))
    with pytest.raises(LLMError) as exc_info:
        LocalLLM("model").complete_text("s", "u", timeout=2.0)
    assert "ollama serve" in str(exc_info.value)

    # модели нет в локальном хранилище
    _patch_urlopen(monkeypatch, _FakeHTTP(
        error=urllib.error.HTTPError("url", 404, "not found", {}, None)  # type: ignore[arg-type]
    ))
    with pytest.raises(LLMError) as exc_info:
        LocalLLM("нет-такой").complete_text("s", "u", timeout=2.0)
    assert "ollama pull" in str(exc_info.value)

    # пустой ответ (бывает у квантованных весов с обрезанным контекстом)
    _patch_urlopen(monkeypatch, _FakeHTTP(body={"choices": [{"message": {"content": "   "}}]}))
    with pytest.raises(LLMError):
        LocalLLM("model").complete_text("s", "u", timeout=2.0)


def test_local_timeout_is_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, _FakeHTTP(error=socket.timeout("slow")))
    with pytest.raises(LLMTimeout):
        LocalLLM("model").complete_text("s", "u", timeout=1.0)


# ---------------------------------------------------------------- цепочка и настройки


def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гарантирует, что чужие переменные окружения не влияют на сборку цепочки."""
    for var in (
        "GIGACHAT_CREDENTIALS", "YANDEX_API_KEY", "YANDEX_IAM_TOKEN", "YANDEX_FOLDER_ID",
        "LLM_PROVIDER", "LLM_TIMEOUT_S", "LOCAL_MODEL", "LOCAL_BASE_URL", "LOCAL_API_KEY",
        # Провайдер «любая API-модель»: и LLM_API_*, и привычные OPENAI_* должны быть выключены,
        # иначе машина с чужим OPENAI_API_KEY в окружении меняет порядок цепочки в тестах.
        "LLM_API_KEY", "LLM_API_BASE_URL", "LLM_API_MODEL", "LLM_API_MAX_TOKENS",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "LLM_JSON_MODE",
    ):
        monkeypatch.delenv(var, raising=False)


def test_auto_prefers_cloud_when_keys_present(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "key")

    # yandex без ключей в цепочку не попадает, mock остаётся последним
    assert [p.name for p in build_chain(get_settings()).providers] == ["gigachat", "mock"]


def test_explicit_order_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "yandex,gigachat")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "key")
    monkeypatch.setenv("YANDEX_API_KEY", "key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "folder")

    assert [p.name for p in build_chain(get_settings()).providers] == ["yandex", "gigachat", "mock"]


def test_auto_uses_local_when_it_is_the_only_free_option(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без облачных ключей, но с локальной моделью цепочка идёт local → mock (это и есть бесплатный контур)."""
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("LOCAL_MODEL", "GigaChat-20B-A3B:q4_K_M")

    assert [p.name for p in build_chain(get_settings()).providers] == ["local", "mock"]


def test_cloud_is_preferred_over_local_in_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "key")
    monkeypatch.setenv("LOCAL_MODEL", "GigaChat-20B-A3B")

    assert [p.name for p in build_chain(get_settings()).providers] == ["gigachat", "local", "mock"]


def test_local_provider_visible_in_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    status = {item["name"]: item for item in provider_status(get_settings())}
    assert status["local"]["configured"] is False
    assert "LOCAL_MODEL" in status["local"]["missing"][0]
    monkeypatch.setenv("LOCAL_MODEL", "model")
    assert {i["name"]: i["configured"] for i in provider_status(get_settings())}["local"] is True


def test_chain_without_providers_is_rejected() -> None:
    """Пустая цепочка означала бы «клиент не получит ответ» — такого состояния быть не должно."""
    with pytest.raises(ValueError):
        FallbackChain([])


def test_any_api_model_replaces_vendor_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Любая API-модель» встаёт первой: вендорам ключи больше не нужны, mock остаётся замыкающим."""
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-any")
    monkeypatch.setenv("LLM_API_MODEL", "deepseek-chat")
    monkeypatch.setenv("LLM_API_BASE_URL", "https://api.deepseek.com/v1")

    chain = build_chain(get_settings())
    assert [p.name for p in chain.providers] == ["api", "mock"]
    assert chain.name == "api→mock"
    assert isinstance(chain.providers[0], AnyApiLLM)


def test_api_goes_before_gigachat_in_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если заданы и свой API, и GigaChat — отвечает API-модель, GigaChat остаётся запасом."""
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-any")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "key")

    assert [p.name for p in build_chain(get_settings()).providers] == ["api", "gigachat", "mock"]


def test_provider_alias_and_explicit_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """`LLM_PROVIDER=openai`/`openrouter` — это тот же провайдер `api`; список задаёт порядок."""
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-any")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter,local")

    assert [p.name for p in build_chain(get_settings()).providers] == ["api", "mock"]

    monkeypatch.setenv("LLM_PROVIDER", "gigachat,api")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "key")
    assert [p.name for p in build_chain(get_settings()).providers] == ["gigachat", "api", "mock"]


def test_example_env_does_not_enable_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` скопировали как есть → пустые LLM_API_* не включают провайдера: отвечает mock."""
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("LLM_API_BASE_URL", "")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_API_MODEL", "")

    assert [p.name for p in build_chain(get_settings()).providers] == ["mock"]


def test_api_gateway_without_key_needs_explicit_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Внутренний шлюз без авторизации: явный LLM_API_BASE_URL сам включает провайдера."""
    _isolate(monkeypatch)
    assert "api" not in [p.name for p in build_chain(get_settings()).providers]

    monkeypatch.setenv("LLM_API_BASE_URL", "http://llm-gateway.internal/v1")
    monkeypatch.setenv("LLM_API_MODEL", "internal-8b")
    chain = build_chain(get_settings())
    assert [p.name for p in chain.providers] == ["api", "mock"]
    assert chain.providers[0].url == "http://llm-gateway.internal/v1/chat/completions"


def test_openai_env_aliases_are_picked_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Привычные OPENAI_* тоже работают: подключать чужой ключ не надо."""
    _isolate(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1/")

    chain = build_chain(get_settings())
    provider = chain.providers[0]
    assert provider.name == "api" and provider.model == "gpt-4.1-mini"
    assert provider.url == "https://gateway.example/v1/chat/completions"


def test_api_provider_visible_in_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    status = {item["name"]: item for item in provider_status(get_settings())}
    assert status["api"]["configured"] is False
    assert "LLM_API_KEY" in status["api"]["missing"][0]

    monkeypatch.setenv("LLM_API_KEY", "sk-any")
    status = {item["name"]: item for item in provider_status(get_settings())}
    assert status["api"]["configured"] is True

    summary = describe_llm(get_settings())
    assert summary["llm"] == "api→mock" and summary["llm_ready"]["api"] is True


def test_api_model_answers_again_after_network_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Шлюз отвалился — отвечает mock; сеть вернулась — снова отвечает API-модель."""
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_API_KEY", "sk-any")
    monkeypatch.setenv("LLM_API_MODEL", "gpt-4o-mini")

    _patch_urlopen(monkeypatch, _FakeHTTP(error=urllib.error.URLError("network is unreachable")))
    chain = build_chain(get_settings())
    assert chain.complete_text("s", "Скидка?", timeout=3.0)  # ответил mock, исключения нет

    _patch_urlopen(monkeypatch, _FakeHTTP(body={"choices": [{"message": {"content": "ответ API"}}]}))
    assert build_chain(get_settings()).complete_text("s", "Скидка?", timeout=3.0) == "ответ API"


def test_mock_only_mode_never_calls_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "key")

    assert [p.name for p in build_chain(get_settings()).providers] == ["mock"]


def test_broken_cloud_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключи есть, но сеть до API не работает — клиент всё равно получает ответ движка."""
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    monkeypatch.setenv("YANDEX_API_KEY", "key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "folder")
    _patch_urlopen(monkeypatch, _FakeHTTP(error=urllib.error.URLError("network is unreachable")))

    chain = build_chain(get_settings())
    assert [p.name for p in chain.providers] == ["yandex", "mock"]
    result = chain.complete_json("s", "<client_message>Дайте скидку на 10 принтеров</client_message>", None, timeout=3.0)
    assert result["intent"] == "discount_request"  # ответил mock


def test_provider_status_and_describe(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    status = {item["name"]: item for item in provider_status(get_settings())}
    assert status["gigachat"]["missing"] == ["GIGACHAT_CREDENTIALS"]
    assert status["yandex"]["missing"] == ["YANDEX_API_KEY (или YANDEX_IAM_TOKEN)", "YANDEX_FOLDER_ID"]
    assert status["mock"]["configured"] is True

    summary = describe_llm(get_settings())
    assert summary["llm"] == "mock" and summary["llm_ready"]["mock"] is True
    assert summary["llm_missing"]["gigachat"] == ["GIGACHAT_CREDENTIALS"]


def test_timeout_budget_comes_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_TIMEOUT_S", "7.5")
    assert default_timeout() == 7.5


def test_unknown_provider_name_falls_back_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    chain = build_chain(get_settings())
    assert isinstance(chain.providers[-1], MockLLM) and len(chain.providers) == 1


# ---------------------------------------------------------------- health


@pytest.fixture()
def api(db: Any) -> Any:
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


def test_api_health_reports_llm_chain(api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)
    body = api.get("/api/health").json()
    assert body["provider"] == "mock"
    assert body["llm"] == "mock" and body["llm_ready"]["mock"] is True
    assert body["llm_missing"]["yandex"] == ["YANDEX_API_KEY (или YANDEX_IAM_TOKEN)", "YANDEX_FOLDER_ID"]


def test_site_health_reports_llm_chain(api: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Публичный health сайта: интерфейс видит, облако отвечает клиентам или демо-движок."""
    _isolate(monkeypatch)
    body = api.get("/api/site/health").json()
    assert body["llm"] == "mock" and "llm_missing" in body


# ---------------------------------------------------------------- контракт реального SDK


def test_payload_is_valid_for_installed_gigachat_sdk() -> None:
    """Если SDK установлен — проверяем, что наш payload принимают его модели (сеть не нужна)."""
    models = pytest.importorskip("gigachat.models")
    payload = {
        "messages": [
            {"role": "system", "content": "Ты менеджер"},
            {"role": "user", "content": "Скидка?"},
        ],
        "temperature": 0.2,
        "model": "GigaChat-2",
        "max_tokens": 200,
    }
    # старый контракт (0.1.x и совместимый шим 0.2.x) и новый контракт v2
    assert models.Chat.model_validate(payload)
    if hasattr(models, "ChatCompletionRequest"):
        assert models.ChatCompletionRequest.model_validate(payload)
