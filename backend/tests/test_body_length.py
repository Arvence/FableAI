import asyncio
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.contracts import ChatTurnRequest, DraftBatch, EntryContent, GeneratedEntry, GenerationRequest, ReferenceCollection
from app.conversation_contracts import GenerationPreferences, TurnPlan
from app.conversation_service import ConversationService
from app.gemini_provider import GeminiProvider
from app.synthetic_generation import SyntheticGenerationService


TOPIC = "Create recent new about Satisfactory game"
BODY = "In this fictional Satisfactory expansion, pioneers build modular factories above a glowing canyon. The mock update adds aerial logistics, unusual power systems, and new ways to coordinate a shared production line."


def content(body: str = BODY, **changes: object) -> dict:
    return {"title": "[FICTIONAL] Satisfactory Imagines Skybound Factories", "body": body, "category": "news", "language": "en", "tags": ["fiction"], "is_synthetic": True, "status": "draft", **changes}


def provider_with(*responses: object) -> Mock:
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(side_effect=responses)
    return provider


def request(**changes: object) -> GenerationRequest:
    return GenerationRequest(conversation_id=uuid4(), topic="Satisfactory game", count=1, tone="neutral", category="news", language="en", min_body_chars=700, max_body_chars=1200, **changes)


@pytest.mark.parametrize("length", [699, 700, 900, 1200, 1201])
def test_target_length_keeps_original_text_and_reports_only_outside_target(length: int) -> None:
    body = (BODY + " ") * 6
    body = body[:length - 1] + "."
    batch = DraftBatch(entries=[content(body)])
    before = batch.model_dump_json()
    provider = provider_with(batch)
    result = asyncio.run(SyntheticGenerationService(provider).generate(request(), ReferenceCollection(), strict_body_length=False))
    assert result.kind == "generation" and result.entries[0].body == body
    assert ("Length notice:" in result.assistant_message) is (length < 700 or length > 1200)
    if length < 700 or length > 1200:
        assert f"draft 1: {length} characters" in result.assistant_message
        assert "target 700-1200" in result.assistant_message
    assert batch.model_dump_json() == before
    provider.generate.assert_awaited_once()


def test_long_selection_reaches_generation_without_invented_interpreter_bounds() -> None:
    provider = provider_with(TurnPlan(action="generate", preferences=GenerationPreferences(min_body_chars=100, max_body_chars=300)), DraftBatch(entries=[content()]))
    settings = {"body_length_mode": "target", "count": 1, "category": "news", "min_body_chars": 700, "max_body_chars": 1200}
    result = asyncio.run(ConversationService(provider).handle_turn(ChatTurnRequest(instruction=TOPIC, generation_settings=settings)))
    assert result.response.kind == "generation"
    assert result.response.entries[0].body == BODY
    assert "Length notice:" in result.response.assistant_message
    assert result.snapshot.preferences.min_body_chars == 700 and result.snapshot.preferences.max_body_chars == 1200
    interpretation = provider.generate.call_args_list[0].args[0]
    assert "body_length_mode" not in interpretation
    data = json.JSONDecoder().raw_decode(provider.generate.call_args_list[1].args[0].split("DATA_JSON:\n", 1)[1])[0]
    assert data["constraints"]["min_body_chars"] == 700 and data["constraints"]["max_body_chars"] == 1200
    assert provider.generate.await_count == 2


@pytest.mark.parametrize("settings", [
    {"min_body_chars": 700, "max_body_chars": 1200},
    {"body_length_mode": "strict", "min_body_chars": 700, "max_body_chars": 1200},
    {"body_length_mode": "target", "min_body_chars": 100, "max_body_chars": 300},
])
@pytest.mark.parametrize("limit", ["between 700 and 1200 characters", "a 700-1200-character body"])
def test_explicit_numeric_limits_remain_strict(settings: dict, limit: str) -> None:
    provider = provider_with(TurnPlan(action="generate", preferences=GenerationPreferences(min_body_chars=700, max_body_chars=1200)), DraftBatch(entries=[content()]))
    turn = ChatTurnRequest(instruction=TOPIC + ", " + limit + ".", generation_settings={**settings, "category": "news"})
    result = asyncio.run(ConversationService(provider).handle_turn(turn))
    assert result.response.kind == "error"
    assert str(len(BODY)) in result.response.error.message and "700-1200" in result.response.error.message
    assert result.snapshot is None
    assert provider.generate.await_count == 2


@pytest.mark.parametrize("change", [{"language": "fr"}, {"category": "blog"}, {"title": "Missing fiction label"}, {"body": BODY + " Visit https://example.com for evidence."}])
def test_target_lengths_do_not_disable_other_output_checks(change: dict) -> None:
    provider = provider_with(DraftBatch(entries=[content(**change)]))
    result = asyncio.run(SyntheticGenerationService(provider).generate(request(), ReferenceCollection(), strict_body_length=False))
    assert result.kind == "error" and result.error.code == "generation_failed"
    provider.generate.assert_awaited_once()


def test_actual_batch_budget_still_applies_to_target_lengths() -> None:
    entries = [content(chr(97 + index) * 4100, title=f"[FICTIONAL] Independent design {index}") for index in range(3)]
    provider = provider_with(DraftBatch(entries=entries))
    constraints = request().model_copy(update={"count": 3})
    result = asyncio.run(SyntheticGenerationService(provider).generate(constraints, ReferenceCollection(), strict_body_length=False))
    assert result.kind == "error" and "12000-character batch limit" in result.error.message
    provider.generate.assert_awaited_once()


def test_actual_conversation_budget_still_applies_to_target_lengths() -> None:
    previous = [GeneratedEntry(id=uuid4(), version=1, **content(chr(97 + index) * 5800, title=f"[FICTIONAL] Previous design {index}")) for index in range(4)]
    provider = provider_with(DraftBatch(entries=[content("z" * 1201)]))
    result = asyncio.run(SyntheticGenerationService(provider).generate(request(), ReferenceCollection(), strict_body_length=False, existing_entries=previous))
    assert result.kind == "error" and "24000-character draft limit" in result.error.message


def test_target_lengths_still_reject_empty_and_oversized_bodies() -> None:
    for body in ("", "x" * 6001):
        provider = provider_with(DraftBatch.model_construct(entries=[EntryContent.model_construct(**content(body))]))
        result = asyncio.run(SyntheticGenerationService(provider).generate(request(), ReferenceCollection(), strict_body_length=False))
        assert result.kind == "error"
