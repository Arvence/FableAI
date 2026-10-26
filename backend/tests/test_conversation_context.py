import asyncio
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.contracts import MAX_ACTIVE_BODY_CHARS, MAX_ACTIVE_ENTRIES, MAX_CONTEXT_CHARS, MAX_CONTEXT_MESSAGES, ChatMessage, ChatTurnRequest, DraftBatch, GeneratedEntry, ReferenceCollection, ReferenceText, SourcePermission, URLReference
from app.conversation_context import MAX_CONVERSATION_CONTEXT_CHARS, MAX_ENTRY_PREVIEW_CHARS, MAX_RECENT_MESSAGE_CHARS, MAX_RECENT_MESSAGES, MAX_TURN_PROMPT_CHARS, build_turn_prompt, encode_drafting_context
from app.conversation_contracts import ConversationSnapshot, GenerationPreferences, PendingTurn, TurnPlan
from app.conversation_service import ConversationService
from app.gemini_provider import GeminiProvider


def active_entry(index: int = 0, length: int = 1_000) -> GeneratedEntry:
    return GeneratedEntry(id=uuid4(), version=1, title=f"[FICTIONAL] Imaginary exhibition number {index}", body="a" * length, category="announcement", tags=["fiction"], language="en", is_synthetic=True, status="draft")


def preferences(**changes: object) -> GenerationPreferences:
    return GenerationPreferences(topic="An imaginary exhibition", count=1, tone="neutral", category="announcement", language="en", min_body_chars=100, max_body_chars=500, **changes)


def provider() -> Mock:
    result = Mock(spec=GeminiProvider)
    result.generate = AsyncMock(return_value=TurnPlan(action="message"))
    return result


def test_context_uses_recent_bounded_messages_and_entry_previews() -> None:
    conversation_id = uuid4()
    messages = [ChatMessage(conversation_id=conversation_id, message_id=uuid4(), role="user", content=f"Turn {index}: " + "x" * 1_000) for index in range(20)]
    state = ConversationSnapshot(conversation_id=conversation_id, messages=messages, active_entries=[active_entry()], stable_instructions=["Keep headings compact."], preferences=preferences())
    before = state.model_dump_json()

    context = json.loads(encode_drafting_context(state, "Generate one more."))

    assert len(context["recent_messages"]) == MAX_RECENT_MESSAGES
    assert all(len(message["content"]) <= MAX_RECENT_MESSAGE_CHARS and message["truncated"] for message in context["recent_messages"])
    assert context["omitted_message_count"] == 14
    assert len(context["active_entries"][0]["body_preview"]) == MAX_ENTRY_PREVIEW_CHARS
    assert context["active_entries"][0]["body_truncated"] is True
    assert context["stable_instructions"] == ["Keep headings compact."]
    assert context["preferences"]["tone"] == "neutral"
    assert state.model_dump_json() == before


def test_maximum_active_snapshot_reduces_optional_context_without_dropping_identity() -> None:
    conversation_id = uuid4()
    state = ConversationSnapshot(
        conversation_id=conversation_id,
        messages=[ChatMessage(conversation_id=conversation_id, message_id=uuid4(), role="user", content="m" * 500) for _ in range(20)],
        active_entries=[active_entry(index) for index in range(MAX_ACTIVE_ENTRIES)],
        stable_instructions=["s" * 4_000],
        pending=PendingTurn(action="revise", instruction="p" * 4_000),
    )
    for entry in state.active_entries:
        entry.title = "[FICTIONAL] " + "h" * 188
    request = ChatTurnRequest(conversation_id=conversation_id, instruction="i" * 4_000)
    context = encode_drafting_context(state, request.instruction)
    data = json.loads(context)
    prompt = build_turn_prompt(request, state)
    assert len(context) <= MAX_CONVERSATION_CONTEXT_CHARS
    assert len(prompt) <= MAX_TURN_PROMPT_CHARS
    assert {entry["id"] for entry in data["active_entries"]} == {str(entry.id) for entry in state.active_entries}
    assert data["stable_instructions"] == state.stable_instructions
    assert data["pending"]["instruction"] == state.pending.instruction
    assert data["current_instruction"] == request.instruction
    assert data["omitted_message_count"] > 14


def test_turn_history_evicts_oldest_messages_and_keeps_preferences_and_references() -> None:
    conversation_id = uuid4()
    state = ConversationSnapshot(
        conversation_id=conversation_id,
        messages=[ChatMessage(conversation_id=conversation_id, message_id=uuid4(), role="user", content=f"Earlier message {index}") for index in range(MAX_CONTEXT_MESSAGES)],
        stable_instructions=["Retain the imaginary setting."],
        references=ReferenceCollection(examples=[ReferenceText(id=uuid4(), text="An authorized synthetic reference.")]),
        preferences=preferences(),
    )
    before = state.model_dump_json()
    result = asyncio.run(ConversationService(provider()).handle_turn(ChatTurnRequest(conversation_id=conversation_id, instruction="Keep these preferences."), state))
    assert result.response.kind == "message"
    assert len(result.snapshot.messages) == MAX_CONTEXT_MESSAGES
    assert result.snapshot.messages[0].content == "Earlier message 2"
    assert result.snapshot.messages[-1].role == "assistant"
    assert result.snapshot.preferences == state.preferences
    assert result.snapshot.references == state.references
    assert result.snapshot.stable_instructions == state.stable_instructions
    assert state.model_dump_json() == before


def test_history_character_budget_accounts_for_newly_added_references() -> None:
    conversation_id = uuid4()
    state = ConversationSnapshot(conversation_id=conversation_id, messages=[ChatMessage(conversation_id=conversation_id, message_id=uuid4(), role="user", content="m" * 4_000) for _ in range(10)])
    request = ChatTurnRequest(conversation_id=conversation_id, instruction="Save these examples.", examples=["a" * 10_000, "b" * 10_000, "c" * 10_000])
    result = asyncio.run(ConversationService(provider()).handle_turn(request, state))
    assert result.response.kind == "message"
    assert len(result.snapshot.messages) < len(state.messages)
    assert sum(len(message.content) for message in result.snapshot.messages) + result.snapshot.references.character_count <= MAX_CONTEXT_CHARS
    assert len(result.snapshot.references.examples) == 3


@pytest.mark.parametrize("permission", [False, True])
def test_only_permitted_imports_contribute_style_patterns(permission: bool) -> None:
    reference = URLReference(id=uuid4(), url="https://example.com/source", import_status="imported", imported_text="Confidentially distinctive source phrase.", import_permission=SourcePermission(url="https://example.com/source", basis="owned", evidence="Synthetic fixture.", automated_access_allowed=True, model_use_allowed=permission, non_confidential=permission))
    state = ConversationSnapshot(conversation_id=uuid4(), references=ReferenceCollection(urls=[reference]))
    request = ChatTurnRequest(conversation_id=state.conversation_id, instruction="Use my references.")
    prompt = build_turn_prompt(request, state)
    data = json.loads(prompt.split("DATA_JSON:\n", 1)[1])
    assert bool(data["reference_patterns"]["samples"]) is permission
    assert "Confidentially distinctive source phrase." not in prompt
    assert str(reference.url) not in prompt


def test_link_only_references_allow_drafting_without_claiming_source_access() -> None:
    state = ConversationSnapshot(conversation_id=uuid4(), preferences=preferences(), references=ReferenceCollection(urls=[URLReference(id=uuid4(), url="https://example.com/source")]))
    entry = active_entry().model_dump(exclude={"id", "version"})
    entry["body"] = "Inside an imaginary gallery, tiny copper sculptures arrange themselves into a winding path. Painted doors reveal new patterns as visitors turn wooden handles."
    mocked = provider()
    mocked.generate.side_effect = [TurnPlan(action="generate"), DraftBatch(entries=[entry])]
    result = asyncio.run(ConversationService(mocked).handle_turn(ChatTurnRequest(conversation_id=state.conversation_id, instruction="Generate one more."), state))
    assert result.response.kind == "generation"
    assert result.snapshot.references == state.references
    assert mocked.generate.await_count == 2
    data, _ = json.JSONDecoder().raw_decode(mocked.generate.call_args.args[0].split("DATA_JSON:\n", 1)[1])
    assert data["reference_patterns"]["samples"] == []
    assert data["reference_patterns"]["links"][0]["source_text_status"] == "not_provided_for_model_use"
    assert "https://example.com/source" not in mocked.generate.call_args.args[0]


@pytest.mark.parametrize("limit", ["count", "bodies", "batch"])
def test_active_and_output_budgets_block_drafting_before_second_model_call(limit: str) -> None:
    entries = [active_entry(index) for index in range(20)] if limit == "count" else [active_entry(index, 6_000) for index in range(4)] if limit == "bodies" else []
    state = ConversationSnapshot(conversation_id=uuid4(), active_entries=entries, preferences=preferences(), references=ReferenceCollection(examples=[ReferenceText(id=uuid4(), text="A synthetic reference.")]))
    if limit == "batch":
        state.preferences.count = 5
        state.preferences.max_body_chars = 3_000
    mocked = provider()
    mocked.generate.return_value = TurnPlan(action="generate")
    result = asyncio.run(ConversationService(mocked).handle_turn(ChatTurnRequest(conversation_id=state.conversation_id, instruction="Generate five more." if limit == "batch" else "Generate more."), state))
    assert result.response.kind == "clarification"
    assert result.snapshot.active_entries == state.active_entries
    mocked.generate.assert_awaited_once()


@pytest.mark.parametrize("changes", [
    {"stable_instructions": ["a" * 4_000, "b"]},
    {"stable_instructions": ["a"] * 9},
    {"active_entries": [active_entry(index, 1) for index in range(MAX_ACTIVE_ENTRIES + 1)]},
    {"active_entries": [active_entry(index, 6_000) for index in range(MAX_ACTIVE_BODY_CHARS // 6_000 + 1)]},
])
def test_snapshot_bounds_are_explicit(changes: dict) -> None:
    with pytest.raises(ValidationError):
        ConversationSnapshot(conversation_id=uuid4(), **changes)


def test_context_budget_error_does_not_call_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.conversation_context.MAX_CONVERSATION_CONTEXT_CHARS", 1)
    mocked = provider()
    result = asyncio.run(ConversationService(mocked).handle_turn(ChatTurnRequest(instruction="Save my preferences.")))
    assert result.response.kind == "error" and result.response.error.code == "invalid_request"
    assert result.snapshot is None
    mocked.generate.assert_not_awaited()


def test_exceeding_reference_budget_is_atomic_and_does_not_call_model() -> None:
    state = ConversationSnapshot(conversation_id=uuid4(), references=ReferenceCollection(examples=[ReferenceText(id=uuid4(), text=f"Existing example {index}.") for index in range(10)]))
    mocked = provider()
    request = ChatTurnRequest(conversation_id=state.conversation_id, instruction="Add this reference.", examples=["Another distinct example."])
    result = asyncio.run(ConversationService(mocked).handle_turn(request, state))
    assert result.response.kind == "error" and result.response.error.code == "invalid_request"
    assert result.snapshot == state
    mocked.generate.assert_not_awaited()
