import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from google import genai
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.contracts import DraftBatch
from app.conversation_contracts import GenerationPreferences, TurnPlan
from app.conversation_repository import PROCESSING_LEASE_SECONDS
from app.gemini_provider import GeminiProvider
from app.main import create_app


BASE = "/api/v1/conversations"
FAKE_KEY = "fake-reliability-key-never-for-live-use"
FAKE_MODEL = "mock-reliability-model"
SOURCE = "PRIVATE_SOURCE_CANARY: A self-authored fictional workshop sample for offline reliability tests."
PREFERENCES = {"topic": "An imaginary mosaic exhibition", "count": 1, "tone": "neutral", "category": "announcement", "language": "en", "min_body_chars": 60, "max_body_chars": 300}
ENTRY = {"title": "[FICTIONAL] Mosaic Hall Adds Moving Tiles", "body": "In the invented district of Threadmere, colorful tiles glide across small wooden tables. Visitors arrange a different imaginary landscape at each station.", "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"}
PLAN_JSON = TurnPlan(action="generate", preferences=GenerationPreferences(**PREFERENCES)).model_dump_json()
BATCH_JSON = DraftBatch(entries=[ENTRY]).model_dump_json()


@pytest.fixture
def sdk_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable:
    monkeypatch.setattr("google.genai._api_client.get_env_api_key", lambda: None)

    @contextmanager
    def build(replies: list, key: str | None = FAKE_KEY, model: str | None = FAKE_MODEL) -> Iterator[tuple]:
        requests = []
        sequence = iter(replies)

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            reply = next(sequence)
            if isinstance(reply, Exception):
                raise reply
            if isinstance(reply, int):
                return httpx.Response(reply, json={"error": {"code": reply, "status": "RESOURCE_EXHAUSTED" if reply == 429 else "ERROR", "message": FAKE_KEY + SOURCE}})
            candidates = [] if reply is None else [{"finishReason": "STOP", "content": {"role": "model", "parts": [{"text": reply}]}}]
            return httpx.Response(200, json={"candidates": candidates})

        def factory(**kwargs: object) -> genai.Client:
            kwargs["http_options"].async_client_args = {"transport": httpx.MockTransport(handle), "trust_env": False}
            kwargs["http_options"].client_args = {"trust_env": False}
            return genai.Client(**kwargs)

        client_factory = Mock(side_effect=factory)
        settings = Settings(gemini_api_key=key, gemini_model=model, database_path=tmp_path / "reliability.sqlite3")
        application = create_app(settings, provider=GeminiProvider(settings, client_factory=client_factory))
        with TestClient(application, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
            yield client, requests, client_factory
    return build


@pytest.mark.parametrize(("failure", "reason", "status"), [
    (429, "rate_limited", 429),
    (httpx.ReadTimeout(FAKE_KEY + SOURCE), "timeout", None),
    ("not JSON " + SOURCE, "invalid_response", None),
    (None, "invalid_response", None),
    (503, "unavailable", 503),
    (403, "authentication_failed", 403),
])
@pytest.mark.parametrize("stage", ["interpretation", "generation", "revision"])
def test_provider_failures_preserve_safe_details_and_replay_without_new_calls(sdk_api: Callable, caplog: pytest.LogCaptureFixture, failure: object, reason: str, status: int | None, stage: str) -> None:
    replies = {"interpretation": [failure], "generation": [PLAN_JSON, failure], "revision": [PLAN_JSON, BATCH_JSON, TurnPlan(action="revise").model_dump_json(), failure]}[stage]
    with sdk_api(replies) as (client, requests, factory):
        conversation_id = client.post(BASE).json()["conversation_id"]
        url = f"{BASE}/{conversation_id}/messages"
        original_entries = []
        payload = {"instruction": "Generate one fictional draft.", "examples": [SOURCE]}
        if stage == "revision":
            generated = client.post(url, json=payload)
            assert generated.status_code == 200, generated.text
            original_entries = generated.json()["entries"]
            payload = {"instruction": "Rewrite entry 1.", "revision_targets": [{"entry_id": original_entries[0]["id"], "expected_version": 1}]}
        caplog.set_level(logging.INFO)
        headers = {"Idempotency-Key": str(uuid4())}

        response = client.post(url, json=payload, headers=headers)

        assert response.status_code == (429 if reason == "rate_limited" else 502), response.text
        result = response.json()
        assert result["kind"] == "error" and result["message_id"]
        assert "entries" not in result
        expected = [{"field": "provider_failure", "message": reason}]
        if status is not None:
            expected.append({"field": "provider_http_status", "message": str(status)})
        assert result["error"]["details"] == expected
        calls = {"interpretation": 1, "generation": 2, "revision": 4}[stage]
        assert len(requests) == calls
        replay = client.post(url, json=payload, headers=headers)
        assert replay.status_code == response.status_code and replay.json() == result
        assert len(requests) == calls
        assert client.get(url).json()["items"][-1]["response"] == result
        detail = client.get(f"{BASE}/{conversation_id}").json()
        assert detail["entries"] == original_entries and detail["processing"] is False
        assert FAKE_KEY not in response.text + caplog.text
        assert SOURCE not in response.text + caplog.text
        if reason == "rate_limited":
            assert "wait" in result["error"]["message"].lower()
            assert "billing" not in result["error"]["message"].lower()
        assert factory.call_count == calls
        for request, call in zip(requests, factory.call_args_list, strict=True):
            assert str(request.url) == f"https://generativelanguage.googleapis.com/v1beta/models/{FAKE_MODEL}:generateContent"
            assert request.headers["x-goog-api-key"] == FAKE_KEY
            body = json.loads(request.content)
            assert "tools" not in body and "systemInstruction" not in body
            assert body["generationConfig"]["candidateCount"] == 1
            assert body["generationConfig"]["maxOutputTokens"] == 8192
            assert call.kwargs["enterprise"] is False
            assert call.kwargs["http_options"].retry_options.attempts == 1


@pytest.mark.parametrize(("key", "model", "reason"), [(None, FAKE_MODEL, "missing_credentials"), (FAKE_KEY, None, "missing_model")])
def test_missing_configuration_never_constructs_an_sdk_client(sdk_api: Callable, key: str | None, model: str | None, reason: str) -> None:
    with sdk_api([], key, model) as (client, requests, factory):
        conversation_id = client.post(BASE).json()["conversation_id"]
        response = client.post(f"{BASE}/{conversation_id}/messages", json={"instruction": "Save a preference."})
        assert response.status_code == 502
        assert response.json()["error"]["details"] == [{"field": "provider_failure", "message": reason}]
        assert requests == []
        factory.assert_not_called()


def test_database_failure_before_claim_cannot_consume_provider_quota(sdk_api: Callable, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    with sdk_api([]) as (client, requests, factory):
        conversation_id = client.post(BASE).json()["conversation_id"]
        monkeypatch.setattr(client.app.state.repository, "begin", Mock(side_effect=SQLAlchemyError(FAKE_KEY + SOURCE)))
        response = client.post(f"{BASE}/{conversation_id}/messages", json={"instruction": "Save a preference."})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_failed"
        assert response.json()["message_id"] is None
        assert FAKE_KEY not in response.text + caplog.text and SOURCE not in response.text + caplog.text
        assert requests == []
        factory.assert_not_called()


def test_failed_persistence_keeps_submission_claim_and_recovers_without_replaying_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(return_value=TurnPlan(action="message"))
    application = create_app(Settings(database_path=tmp_path / "unavailable.sqlite3"), provider=provider)
    with TestClient(application, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        repository = application.state.repository
        assert repository.database.engine.hide_parameters is True
        conversation_id = client.post(BASE).json()["conversation_id"]
        url = f"{BASE}/{conversation_id}/messages"
        original_finish = repository.finish
        failed_finish = Mock(side_effect=SQLAlchemyError(FAKE_KEY + SOURCE))
        monkeypatch.setattr(repository, "finish", failed_finish)
        headers, payload = {"Idempotency-Key": str(uuid4())}, {"instruction": "Save a preference."}
        response = client.post(url, json=payload, headers=headers)
        assert response.status_code == 503 and response.json()["error"]["code"] == "storage_failed"
        assert response.json()["message_id"] is None
        assert failed_finish.call_count == 2
        assert FAKE_KEY not in response.text + caplog.text and SOURCE not in response.text + caplog.text
        assert client.post(url, json=payload, headers=headers).status_code == 409
        provider.generate.assert_awaited_once()

        monkeypatch.setattr(repository, "finish", original_finish)
        future = datetime.now(UTC) + timedelta(seconds=PROCESSING_LEASE_SECONDS + 1)
        monkeypatch.setattr(repository, "_clock", lambda: future)
        recovered = client.post(url, json=payload, headers=headers)
        assert recovered.status_code == 409 and recovered.json()["error"]["code"] == "processing_interrupted"
        assert client.post(url, json=payload, headers=headers).json() == recovered.json()
        assert client.get(f"{BASE}/{conversation_id}").json()["processing"] is False
        assert client.get(url).json()["items"][-1]["response"] == recovered.json()
        provider.generate.assert_awaited_once()
