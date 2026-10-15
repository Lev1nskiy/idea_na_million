"""LLM-провайдеры: GigaChat и YandexGPT без ключей и без сети.

Тесты проверяют то, что обычно ломается при подключении: какой payload уходит в API,
как читается ответ (оба контракта SDK), как ошибка провайдера превращается в подсказку
и что цепочка fallback доходит до mock. Сеть подменяется, ключи фальшивые.
"""
from __future__ import annotations

import json
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
from core.llm.base import LLMTimeout, default_timeout
from core.llm.gigachat import GigaChatLLM
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


# ---------------------------------------------------------------- цепочка и настройки


def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гарантирует, что чужие переменные окружения не влияют на сборку цепочки."""
    for var in (
        "GIGACHAT_CREDENTIALS", "YANDEX_API_KEY", "YANDEX_IAM_TOKEN", "YANDEX_FOLDER_ID",
        "LLM_PROVIDER", "LLM_TIMEOUT_S",
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
