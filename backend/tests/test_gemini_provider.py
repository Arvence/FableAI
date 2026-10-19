import asyncio
import json
import logging
import traceback
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, Mock

import httpx
import pytest
from google import genai
from google.genai import types
from pydantic import SecretStr, TypeAdapter, ValidationError

from app.config import Settings, load_settings
from app.contracts import ChatResponse, ErrorResponse, MessageResponse
from app.gemini_provider import GeminiProvider, GeminiProviderError, ProviderFailure


FAKE_KEY = "fake-api-key-for-offline-tests"
FAKE_MODEL = "test-user-verified-model"
PROMPT = "Constructed prompt containing private test reference text."
RESPONSE_ADAPTER = TypeAdapter(ChatResponse)


def response_payload(kind: str = "message") -> dict:
    return {
        "conversation_id": "5c7c0c86-4648-4c36-8179-ec06b67b9f16",
        "message_id": "87ddc8ca-8f70-4bb6-a50c-d426e5b0b293",
        "kind": kind,
        "assistant_message": "Fictional drafts are ready." if kind in {"generation", "revision"} else "Which language should the fictional draft use?",
        "entries": [{
            "id": "c39acf3a-075d-4c89-9e2f-1e401867bec6",
            "title": "Imaginary sky garden opens",
            "body": "This fictional announcement describes a garden above an invented city.",
            "category": "announcement",
            "tags": ["fiction"],
            "language": "en",
            "is_synthetic": True,
            "status": "draft",
            "version": 2 if kind == "revision" else 1,
        }] if kind in {"generation", "revision"} else [],
    }


def sdk_response(text: str, finish_reason: str | None = "STOP") -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish_reason}]}


@pytest.fixture
def provider_factory(monkeypatch: pytest.MonkeyPatch) -> Callable:
    monkeypatch.setattr("google.genai._api_client.get_env_api_key", lambda: None)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-unused-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "test-unused-location")
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "https://unused.invalid/")

    def build(handler: Callable, timeout: float = 30.0) -> tuple[GeminiProvider, list[httpx.Request], Mock]:
        requests = []

        async def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            result = handler(request)
            return await result if asyncio.iscoroutine(result) else result

        transport = httpx.MockTransport(handle)

        def create_client(**kwargs: object) -> genai.Client:
            options = kwargs["http_options"]
            options.async_client_args = {"transport": transport, "trust_env": False}
            options.client_args = {"trust_env": False}
            return genai.Client(**kwargs)

        client_factory = Mock(side_effect=create_client)
        settings = Settings(gemini_api_key=FAKE_KEY, gemini_model=FAKE_MODEL, gemini_timeout_seconds=timeout)
        return GeminiProvider(settings, client_factory=client_factory), requests, client_factory

    return build


@pytest.mark.parametrize("kind", ["message", "clarification", "generation", "revision"])
def test_validates_existing_response_contracts(provider_factory: Callable, kind: str) -> None:
    payload = response_payload(kind)
    provider, requests, factory = provider_factory(lambda request: httpx.Response(200, json=sdk_response(json.dumps(payload))))

    result = asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert result.model_dump(mode="json") == payload
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://generativelanguage.googleapis.com/v1beta/models/{FAKE_MODEL}:generateContent"
    assert request.headers["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["contents"] == [{"parts": [{"text": PROMPT}], "role": "user"}]
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"] == RESPONSE_ADAPTER.json_schema()
    assert body["generationConfig"]["candidateCount"] == 1
    assert body["generationConfig"]["maxOutputTokens"] == 8_192
    assert "tools" not in body
    assert "systemInstruction" not in body
    assert FAKE_KEY not in request.content.decode()
    assert request.extensions["timeout"] == {"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}
    assert factory.call_args.kwargs["enterprise"] is False
    assert factory.call_args.kwargs["http_options"].retry_options.attempts == 1


def test_accepts_pydantic_model_class(provider_factory: Callable) -> None:
    provider, requests, _ = provider_factory(lambda request: httpx.Response(200, json=sdk_response(json.dumps(response_payload()))))

    result = asyncio.run(provider.generate(PROMPT, MessageResponse))

    assert isinstance(result, MessageResponse)
    assert json.loads(requests[0].content)["generationConfig"]["responseJsonSchema"] == MessageResponse.model_json_schema()


@pytest.mark.parametrize(("key", "model", "reason"), [
    (None, FAKE_MODEL, ProviderFailure.MISSING_CREDENTIALS),
    ("", FAKE_MODEL, ProviderFailure.MISSING_CREDENTIALS),
    (" \t ", FAKE_MODEL, ProviderFailure.MISSING_CREDENTIALS),
    (FAKE_KEY, None, ProviderFailure.MISSING_MODEL),
    (FAKE_KEY, "", ProviderFailure.MISSING_MODEL),
    (FAKE_KEY, " \t ", ProviderFailure.MISSING_MODEL),
])
def test_missing_configuration_never_creates_client(key: str | None, model: str | None, reason: ProviderFailure, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "unused-google-key")
    factory = Mock()
    provider = GeminiProvider(Settings(gemini_api_key=key, gemini_model=model), client_factory=factory)

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == reason
    assert raised.value.error.code == "generation_failed"
    factory.assert_not_called()


def test_configuration_uses_backend_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GEMINI_MODEL", FAKE_MODEL)
    settings = load_settings()

    assert isinstance(settings.gemini_api_key, SecretStr)
    assert settings.gemini_api_key.get_secret_value() == FAKE_KEY
    assert settings.gemini_model == FAKE_MODEL
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in settings.model_dump_json()


def test_configuration_has_no_default_credentials_or_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "unused-google-key")
    settings = load_settings()

    assert settings.gemini_api_key is None
    assert settings.gemini_model is None
    assert settings.gemini_timeout_seconds == 30.0


@pytest.mark.parametrize("timeout", [0, -1, 61, float("inf"), float("nan"), True, "30"])
def test_rejects_unbounded_or_invalid_timeout(timeout: object) -> None:
    with pytest.raises(ValidationError):
        Settings(gemini_timeout_seconds=timeout)


@pytest.mark.parametrize(("status", "reason"), [
    (400, ProviderFailure.REQUEST_FAILED),
    (401, ProviderFailure.AUTHENTICATION_FAILED),
    (403, ProviderFailure.AUTHENTICATION_FAILED),
    (404, ProviderFailure.REQUEST_FAILED),
    (408, ProviderFailure.TIMEOUT),
    (429, ProviderFailure.RATE_LIMITED),
    (500, ProviderFailure.UNAVAILABLE),
    (503, ProviderFailure.UNAVAILABLE),
    (504, ProviderFailure.TIMEOUT),
])
def test_maps_api_failures_without_retry_or_sensitive_details(provider_factory: Callable, caplog: pytest.LogCaptureFixture, status: int, reason: ProviderFailure) -> None:
    sensitive_message = f"Upstream details: {FAKE_KEY}; {PROMPT}"
    payload = {"error": {"code": status, "status": "RESOURCE_EXHAUSTED" if status == 429 else "ERROR", "message": sensitive_message}}
    provider, requests, _ = provider_factory(lambda request: httpx.Response(status, json=payload))
    caplog.set_level(logging.DEBUG)

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    error = raised.value
    assert error.reason == reason
    assert error.error.code == ("rate_limited" if status == 429 else "generation_failed")
    assert len(requests) == 1
    output = ErrorResponse(kind="error", error=error.error).model_dump_json()
    output += str(error) + repr(error) + "".join(traceback.format_exception(error)) + caplog.text
    assert FAKE_KEY not in output
    assert PROMPT not in output
    assert sensitive_message not in output
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


@pytest.mark.parametrize(("details", "reason"), [
    ([{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}], ProviderFailure.AUTHENTICATION_FAILED),
    ([{"reason": "INVALID_ARGUMENT"}], ProviderFailure.REQUEST_FAILED),
    ([None], ProviderFailure.REQUEST_FAILED),
    (None, ProviderFailure.REQUEST_FAILED),
    ("untrusted error details", ProviderFailure.REQUEST_FAILED),
])
def test_maps_invalid_api_key_and_handles_untrusted_error_details(provider_factory: Callable, details: object, reason: ProviderFailure) -> None:
    payload = {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": FAKE_KEY, "details": details}}
    provider, requests, _ = provider_factory(lambda request: httpx.Response(400, json=payload))

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == reason
    assert raised.value.error.code == "generation_failed"
    assert FAKE_KEY not in "".join(traceback.format_exception(raised.value))
    assert len(requests) == 1


@pytest.mark.parametrize(("exception_type", "reason"), [
    (httpx.ConnectTimeout, ProviderFailure.TIMEOUT),
    (httpx.ReadTimeout, ProviderFailure.TIMEOUT),
    (httpx.WriteTimeout, ProviderFailure.TIMEOUT),
    (httpx.PoolTimeout, ProviderFailure.TIMEOUT),
    (httpx.ConnectError, ProviderFailure.UNAVAILABLE),
    (httpx.ReadError, ProviderFailure.UNAVAILABLE),
])
def test_maps_transport_errors_without_retry(provider_factory: Callable, exception_type: type[Exception], reason: ProviderFailure) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise exception_type(FAKE_KEY)

    provider, requests, _ = provider_factory(fail)
    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == reason
    assert FAKE_KEY not in "".join(traceback.format_exception(raised.value))
    assert len(requests) == 1


def test_total_deadline_cancels_slow_request(provider_factory: Callable) -> None:
    cancelled = []

    async def stall(request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    provider, requests, _ = provider_factory(stall, timeout=0.05)
    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == ProviderFailure.TIMEOUT
    assert len(requests) == 1
    assert cancelled == [True]
    assert requests[0].extensions["timeout"]["read"] == 0.05


@pytest.mark.parametrize("text", ["", " ", "not JSON", "```json\n{}\n```", "{}", "[]", "null", '{"kind":"generation"}', json.dumps({**response_payload(), "extra": "untrusted"})])
def test_rejects_malformed_or_nonconforming_json(provider_factory: Callable, text: str) -> None:
    provider, requests, _ = provider_factory(lambda request: httpx.Response(200, json=sdk_response(text)))

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == ProviderFailure.INVALID_RESPONSE
    assert len(requests) == 1


@pytest.mark.parametrize(("field", "value"), [("version", "1"), ("is_synthetic", False), ("is_synthetic", 1), ("status", "published"), ("body", "")])
def test_enforces_semantic_contract_validation(provider_factory: Callable, field: str, value: object) -> None:
    payload = response_payload("generation")
    payload["entries"][0][field] = value
    provider, requests, _ = provider_factory(lambda request: httpx.Response(200, json=sdk_response(json.dumps(payload))))

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == ProviderFailure.INVALID_RESPONSE
    assert len(requests) == 1


@pytest.mark.parametrize("finish_reason", ["MAX_TOKENS", "SAFETY", "RECITATION", "CONTINUATION", "OTHER", None])
def test_rejects_unfinished_or_blocked_response_without_continuation(provider_factory: Callable, finish_reason: str | None) -> None:
    payload = sdk_response(json.dumps(response_payload()), finish_reason)
    payload["candidates"][0]["continuationToken"] = "dGVzdA=="
    provider, requests, _ = provider_factory(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == ProviderFailure.INVALID_RESPONSE
    assert len(requests) == 1


@pytest.mark.parametrize("payload", [
    {},
    {"candidates": []},
    {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": [{"finishReason": "STOP"}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": []}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}", "thought": True}]}}]},
    {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"functionCall": {"name": "unrequested_tool", "args": {}}}]}}]},
    {"candidates": sdk_response(json.dumps(response_payload()))["candidates"] * 2},
])
def test_rejects_empty_blocked_or_unexpected_content(provider_factory: Callable, payload: dict) -> None:
    provider, requests, _ = provider_factory(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == ProviderFailure.INVALID_RESPONSE
    assert len(requests) == 1


def test_joins_text_parts_and_excludes_thoughts(provider_factory: Callable) -> None:
    text = json.dumps(response_payload())
    payload = sdk_response(text)
    payload["candidates"][0]["content"]["parts"] = [{"text": "Private reasoning", "thought": True}, {"text": text[:50]}, {"text": text[50:]}]
    provider, _, _ = provider_factory(lambda request: httpx.Response(200, json=payload))

    assert asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER)).kind == "message"


@pytest.mark.parametrize("body", ["malformed upstream response", "null", "[]"])
def test_handles_malformed_sdk_envelope(provider_factory: Callable, body: str) -> None:
    provider, requests, _ = provider_factory(lambda request: httpx.Response(200, text=body))

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == ProviderFailure.INVALID_RESPONSE
    assert len(requests) == 1


def test_ignores_sdk_parsed_data_and_closes_both_clients() -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    async_client = MagicMock()
    client.aio.__aenter__.return_value = async_client
    response = types.GenerateContentResponse.model_validate(sdk_response("not JSON"))
    response.parsed = response_payload()
    async_client.models.generate_content = AsyncMock(return_value=response)
    provider = GeminiProvider(Settings(gemini_api_key=FAKE_KEY, gemini_model=FAKE_MODEL), client_factory=Mock(return_value=client))

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == ProviderFailure.INVALID_RESPONSE
    async_client.models.generate_content.assert_awaited_once()
    client.aio.__aexit__.assert_awaited_once()
    client.__exit__.assert_called_once()


@pytest.mark.parametrize("failure", [TimeoutError(FAKE_KEY), httpx.ConnectError(FAKE_KEY), asyncio.CancelledError()])
def test_closes_clients_on_failures_and_preserves_cancellation(failure: BaseException) -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    async_client = MagicMock()
    client.aio.__aenter__.return_value = async_client
    async_client.models.generate_content = AsyncMock(side_effect=failure)
    provider = GeminiProvider(Settings(gemini_api_key=FAKE_KEY, gemini_model=FAKE_MODEL), client_factory=Mock(return_value=client))
    expected_error = asyncio.CancelledError if isinstance(failure, asyncio.CancelledError) else GeminiProviderError

    with pytest.raises(expected_error):
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    async_client.models.generate_content.assert_awaited_once()
    config = async_client.models.generate_content.call_args.kwargs["config"]
    assert config.automatic_continuation is False
    assert config.automatic_function_calling.disable is True
    assert config.tools is None
    client.aio.__aexit__.assert_awaited_once()
    client.__exit__.assert_called_once()


def test_sanitizes_unexpected_client_errors() -> None:
    factory = Mock(side_effect=RuntimeError(f"Secret: {FAKE_KEY}; prompt: {PROMPT}"))
    provider = GeminiProvider(Settings(gemini_api_key=FAKE_KEY, gemini_model=FAKE_MODEL), client_factory=factory)

    with pytest.raises(GeminiProviderError) as raised:
        asyncio.run(provider.generate(PROMPT, RESPONSE_ADAPTER))

    assert raised.value.reason == ProviderFailure.REQUEST_FAILED
    assert FAKE_KEY not in "".join(traceback.format_exception(raised.value))
    assert PROMPT not in "".join(traceback.format_exception(raised.value))
    factory.assert_called_once()
