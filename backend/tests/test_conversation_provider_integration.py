import asyncio
import json
from collections.abc import Callable
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from google import genai

from app.config import Settings
from app.contracts import MAX_ENTRIES, ChatTurnRequest, GeneratedEntry, ReferenceCollection, ReferenceText
from app.conversation_contracts import ConversationSnapshot, GenerationPreferences
from app.conversation_service import ConversationService
from app.gemini_provider import GeminiProvider
from draft_samples import rainy_day_batch


FAKE_KEY = "offline-conversation-test-key"
BODY = "In an imaginary harbor, painted kites assemble a drifting mosaic above a paper pier. Visitors rearrange colorful tiles while the pretend tide turns beneath the exhibition."
SHORT_BODY = "Above an imaginary pier, painted kites carry a floating mosaic. Visitors rearrange its colorful tiles."
CONTENT = {"title": "[FICTIONAL] Painted Kites Build a Harbor Mosaic", "body": BODY, "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"}
PREFERENCES = {"topic": "An imaginary harbor exhibition", "count": 1, "tone": "neutral", "category": "announcement", "language": "en", "min_body_chars": 60, "max_body_chars": 300}


@pytest.fixture
def sdk_conversation(monkeypatch: pytest.MonkeyPatch) -> Callable:
    monkeypatch.setattr("google.genai._api_client.get_env_api_key", lambda: None)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-unused-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "test-unused-location")

    def build(responses: list[str | dict | Callable]) -> tuple[ConversationService, list[dict], Mock]:
        requests = []

        def handle(request: httpx.Request) -> httpx.Response:
            data = json.loads(request.content)
            requests.append(data)
            response = responses.pop(0)
            if callable(response):
                response = response(data)
            text = response if isinstance(response, str) else json.dumps(response)
            return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}]})

        transport = httpx.MockTransport(handle)

        def factory(**kwargs: object) -> genai.Client:
            kwargs["http_options"].async_client_args = {"transport": transport, "trust_env": False}
            kwargs["http_options"].client_args = {"trust_env": False}
            return genai.Client(**kwargs)

        client_factory = Mock(side_effect=factory)
        provider = GeminiProvider(Settings(gemini_api_key=FAKE_KEY, gemini_model="test-user-verified-model"), client_factory=client_factory)
        return ConversationService(provider), requests, client_factory

    return build


def test_three_turn_flow_through_real_sdk_with_in_memory_responses(sdk_conversation: Callable) -> None:
    def revised_response(data: dict) -> dict:
        prompt = data["contents"][0]["parts"][0]["text"]
        revision = json.loads(prompt.split("REVISION_JSON:\n", 1)[1].split("\nUse the following conversation data", 1)[0])
        return {"entries": [{"entry_id": revision["targets"][0]["entry_id"], **CONTENT, "body": SHORT_BODY}]}

    service, requests, factory = sdk_conversation([
        {"action": "message", "preferences": PREFERENCES, "stable_instruction": "Keep titles short."},
        {"action": "generate"},
        {"entries": [CONTENT]},
        {"action": "revise", "length_change": "shorter"},
        revised_response,
    ])
    first = asyncio.run(service.handle_turn(ChatTurnRequest(instruction="Save these preferences. Keep titles short.", examples=["A self-authored synthetic reference uses a short introductory paragraph."])))
    second = asyncio.run(service.handle_turn(ChatTurnRequest(conversation_id=first.snapshot.conversation_id, instruction="Generate one draft."), first.snapshot))
    third = asyncio.run(service.handle_turn(ChatTurnRequest(conversation_id=second.snapshot.conversation_id, instruction="Make it shorter."), second.snapshot))

    assert [first.response.kind, second.response.kind, third.response.kind] == ["message", "generation", "revision"]
    assert third.response.entries[0].id == second.response.entries[0].id
    assert third.response.entries[0].version == 2
    assert third.response.entries[0].body == SHORT_BODY
    assert len(requests) == 5
    assert [data["generationConfig"]["responseJsonSchema"]["title"] for data in requests] == ["TurnPlan", "TurnPlan", "DraftBatch", "TurnPlan", "RevisionBatch"]
    for data in requests:
        assert data["generationConfig"]["responseMimeType"] == "application/json"
        assert "systemInstruction" not in data and "tools" not in data
        assert FAKE_KEY not in json.dumps(data)
    assert all(call.kwargs["http_options"].retry_options.attempts == 1 for call in factory.call_args_list)


@pytest.mark.parametrize("response", [
    "not JSON",
    "null",
    "[]",
    {},
    {"action": "publish"},
    {"action": "message", "preferences": {"count": "3"}},
    {"action": "generate", "entry_ids": [str(uuid4())]},
    {"action": "revise", "preferences": {"tone": "official"}},
])
def test_malformed_or_unsupported_turn_plan_returns_safe_error(sdk_conversation: Callable, response: object) -> None:
    service, requests, _ = sdk_conversation([response])
    result = asyncio.run(service.handle_turn(ChatTurnRequest(instruction="Use these examples.")))
    assert result.response.kind == "error" and result.response.error.code == "generation_failed"
    assert result.snapshot is None
    assert len(requests) == 1


@pytest.mark.parametrize("defect", ["missing_id", "model_version", "not_synthetic", "not_draft", "malformed"])
def test_malformed_revision_json_leaves_state_unchanged(sdk_conversation: Callable, defect: str) -> None:
    entry = GeneratedEntry(id=uuid4(), version=1, **CONTENT)
    state = ConversationSnapshot(conversation_id=uuid4(), active_entries=[entry], preferences=GenerationPreferences(**PREFERENCES), references=ReferenceCollection(examples=[ReferenceText(id=uuid4(), text="An authorized fictional example.")]))
    revision = {"entry_id": str(entry.id), **CONTENT, "body": SHORT_BODY}
    if defect == "missing_id":
        del revision["entry_id"]
    elif defect == "model_version":
        revision["version"] = 99
    elif defect == "not_synthetic":
        revision["is_synthetic"] = False
    elif defect == "not_draft":
        revision["status"] = "published"
    data = "broken JSON" if defect == "malformed" else {"entries": [revision]}
    service, requests, _ = sdk_conversation([{"action": "revise"}, data])
    result = asyncio.run(service.handle_turn(ChatTurnRequest(conversation_id=state.conversation_id, instruction="Rewrite entry 1."), state))
    assert result.response.kind == "error" and result.response.error.code == "generation_failed"
    assert result.snapshot == state and state.active_entries[0].version == 1
    assert len(requests) == 2


def test_topic_only_flow_through_real_sdk_uses_defaults_and_no_references(sdk_conversation: Callable) -> None:
    service, requests, factory = sdk_conversation([
        {"action": "generate", "preferences": {"topic": "an imaginary harbor exhibition"}},
        {"entries": [CONTENT]},
    ])
    result = asyncio.run(service.handle_turn(ChatTurnRequest(instruction="an imaginary harbor exhibition")))
    assert result.response.kind == "generation"
    assert len(result.response.entries) == 1
    assert len(requests) == 2
    interpretation = requests[0]["contents"][0]["parts"][0]["text"]
    plan_data = json.loads(interpretation.split("DATA_JSON:\n", 1)[1])
    assert plan_data["max_entries_per_turn"] == MAX_ENTRIES
    assert plan_data["generation_defaults"] == {"count": 1, "tone": "neutral", "category": "announcement", "language": "en", "min_body_chars": 100, "max_body_chars": 300}
    drafting = requests[1]["contents"][0]["parts"][0]["text"]
    draft_data, _ = json.JSONDecoder().raw_decode(drafting.split("DATA_JSON:\n", 1)[1])
    assert draft_data["constraints"] == {"mode": "synthetic", "topic": "an imaginary harbor exhibition", **plan_data["generation_defaults"]}
    assert draft_data["reference_patterns"]["samples"] == []
    assert requests[1]["generationConfig"]["responseJsonSchema"]["properties"]["entries"]["maxItems"] == MAX_ENTRIES
    assert all(call.kwargs["http_options"].retry_options.attempts == 1 for call in factory.call_args_list)


def test_sdk_can_extract_out_of_range_count_for_backend_clarification(sdk_conversation: Callable) -> None:
    service, requests, factory = sdk_conversation([{"action": "generate", "preferences": {"topic": "rainy day", "count": MAX_ENTRIES + 1}}])
    result = asyncio.run(service.handle_turn(ChatTurnRequest(instruction=f"create {MAX_ENTRIES + 1} rainy day drafts")))
    assert result.response.kind == "clarification"
    assert f"between 1 and {MAX_ENTRIES}" in result.response.assistant_message
    assert result.snapshot.preferences.count is None
    assert result.snapshot.preferences.topic == f"{MAX_ENTRIES + 1} rainy day drafts"
    assert len(requests) == 1
    assert factory.call_count == 1
    schema = requests[0]["generationConfig"]["responseJsonSchema"]
    count_schema = schema["$defs"]["InterpretedPreferences"]["properties"]["count"]
    assert "maximum" not in json.dumps(count_schema)


@pytest.mark.parametrize("count", [10, MAX_ENTRIES])
def test_larger_batches_roundtrip_through_sdk_schema_and_output_validation(sdk_conversation: Callable, count: int) -> None:
    service, requests, factory = sdk_conversation([
        {"action": "generate", "preferences": {"topic": "rainy day", "count": count}},
        rainy_day_batch(count).model_dump(mode="json"),
    ])
    result = asyncio.run(service.handle_turn(ChatTurnRequest(instruction=f"create {count} rainy day drafts")))
    assert result.response.kind == "generation"
    assert len(result.response.entries) == count
    assert all(100 <= len(entry.body) <= 300 for entry in result.response.entries)
    assert len(requests) == factory.call_count == 2
    prompt = requests[1]["contents"][0]["parts"][0]["text"]
    data, _ = json.JSONDecoder().raw_decode(prompt.split("DATA_JSON:\n", 1)[1])
    assert data["constraints"]["count"] == count
    assert requests[1]["generationConfig"]["responseJsonSchema"]["properties"]["entries"]["maxItems"] == MAX_ENTRIES
    assert all(call.kwargs["http_options"].retry_options.attempts == 1 for call in factory.call_args_list)
