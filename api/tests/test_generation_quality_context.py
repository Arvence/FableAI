import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.contracts import ChatTurnRequest, DraftBatch, GeneratedEntry, ReferenceCollection
from app.conversation_contracts import ConversationSnapshot, GenerationPreferences
from app.generation.conversation_context import build_turn_prompt, encode_drafting_context
from app.generation.gemini_provider import GeminiProvider
from app.generation.revision_generation import RevisionBatch, RevisionGenerationService
from app.generation.synthetic_generation import SyntheticGenerationService
from test_synthetic_generation import generation_request


CASES = [
    ("Create a WoW Forever news article about an Undead Warlock who just started leveling in Deathknell, Tirisfal Glades.", "news", "Undead Warlock Takes First Steps in Deathknell", "An Undead Warlock begins leveling in Deathknell, Tirisfal Glades. A difficult first encounter prompts the novice to change tactics before attempting the next task."),
    ("Write a Star Trek announcement about a science crew opening a new stellar cartography lab.", "announcement", "Science Crew Opens Stellar Cartography Lab", "A Star Trek science crew opens a stellar cartography lab. Its first assignment is to compare conflicting navigation readings before the ship resumes its survey."),
    ("Write a Dune update about a Fremen water conservation trial on Arrakis.", "update", "Fremen Water Trial Reaches Its Next Stage", "A Fremen water conservation trial on Arrakis reaches its next stage. The team adjusts its collection schedule after finding that an earlier shift preserves more water."),
    ("Write an article about a Mars colony repairing its greenhouse after a dust storm.", "article", "Mars Colony Rebuilds Its Greenhouse", "A Mars colony repairs its greenhouse after a dust storm. With seedlings exposed, the maintenance crew prioritizes sealing the growing beds before replacing the damaged outer panels."),
    ("Write a blog about a dragon courier in my invented city of Reedhaven who loses a delivery.", "blog", "Reedhaven Courier Retraces a Lost Delivery", "A dragon courier in Reedhaven loses a delivery during a crowded morning run. Retracing the route reveals a swapped satchel, giving the courier a chance to recover the parcel before dusk."),
]


@pytest.mark.parametrize("instruction,category,title,body", CASES)
def test_generation_preserves_subject_format_and_full_user_context(instruction, category, title, body) -> None:
    request = generation_request(topic=instruction[:200], category=category, tone="informative")
    state = ConversationSnapshot(conversation_id=request.conversation_id, preferences=GenerationPreferences(**request.model_dump(exclude={"conversation_id", "mode"})), stable_instructions=["Use short paragraphs."])
    context = encode_drafting_context(state, instruction)
    payload = {"entries": [{"title": "[FICTIONAL] " + title, "body": body, "category": category, "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"}]}
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(return_value=DraftBatch.model_validate(payload))

    response = asyncio.run(SyntheticGenerationService(provider).generate(request, ReferenceCollection(), context=context))

    assert response.kind == "generation"
    assert response.entries[0].body == body
    assert response.entries[0].category == category
    prompt, schema = provider.generate.call_args.args
    assert schema is DraftBatch
    data, encoded_context = prompt.split("DATA_JSON:\n", 1)[1].split("\nCONVERSATION_JSON:\n", 1)
    data = json.JSONDecoder().raw_decode(data)[0]
    assert data["constraints"] == request.model_dump(mode="json", exclude={"conversation_id"})
    assert json.loads(encoded_context)["current_instruction"] == instruction
    assert json.loads(encoded_context)["stable_instructions"] == ["Use short paragraphs."]
    assert "demonstrations" not in data
    assert all(name not in prompt for name in ("Copperwhistle", "Gloomhaven", "Glass Lagoon"))
    assert "do not replace a named fictional universe" in prompt
    assert "when uncertain, stay within supplied details" in prompt
    assert response.entries[0].title.startswith("[FICTIONAL]")
    assert response.entries[0].is_synthetic and response.entries[0].status == "draft"


@pytest.mark.parametrize("instruction,category,title,body", CASES)
def test_revision_keeps_original_setting_context_and_user_preferences(instruction, category, title, body) -> None:
    request = generation_request(topic=instruction[:200], category=category, tone="informative")
    original = GeneratedEntry(id=request.conversation_id, version=1, title="[FICTIONAL] " + title, body=body, category=category, tags=["fiction"], language="en", is_synthetic=True, status="draft")
    state = ConversationSnapshot(conversation_id=request.conversation_id, preferences=GenerationPreferences(**request.model_dump(exclude={"conversation_id", "mode"})), active_entries=[original], stable_instructions=["Use short paragraphs."])
    current = "Revise entry 1: emphasize the character's motivation; keep the setting and format."
    context = encode_drafting_context(state, current)
    revised_body = body + " The next step will test that decision."
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(return_value=RevisionBatch.model_validate({"entries": [{"entry_id": original.id, **original.model_dump(exclude={"id", "version"}), "body": revised_body}]}))

    entries = asyncio.run(RevisionGenerationService(provider).revise([request], [original], ReferenceCollection(), context=context, other_entries=[]))

    assert entries[0].id == original.id and entries[0].version == 2
    assert entries[0].body == revised_body
    prompt, schema = provider.generate.call_args.args
    assert schema is RevisionBatch
    data = json.JSONDecoder().raw_decode(prompt.split("REVISION_JSON:\n", 1)[1])[0]
    target = data["targets"][0]
    assert target["original"] == original.model_dump(mode="json", exclude={"id", "version"})
    assert target["constraints"] == request.model_dump(mode="json", exclude={"conversation_id"})
    assert "demonstrations" not in data
    encoded = json.loads(prompt.split("CONVERSATION_JSON:\n", 1)[1])
    assert encoded["current_instruction"] == current
    assert encoded["stable_instructions"] == ["Use short paragraphs."]
    assert encoded["preferences"]["topic"] == instruction[:200]
    assert "Keep each target's fictional subject and continuity" in prompt


@pytest.mark.parametrize("instruction,category,title,body", CASES)
def test_interpretation_carries_named_subject_and_supported_formats(instruction, category, title, body) -> None:
    request = ChatTurnRequest(instruction=instruction)
    prompt = build_turn_prompt(request, ConversationSnapshot(conversation_id=generation_request().conversation_id))
    data = json.loads(prompt.split("DATA_JSON:\n", 1)[1])
    assert data["conversation"]["current_instruction"] == instruction
    assert "categories are news, announcement, update, article, and blog" in prompt
    assert "do not generalize them into a different genre" in prompt
