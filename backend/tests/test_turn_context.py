import asyncio
import json
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.contracts import ChatTurnRequest, DraftBatch, GeneratedEntry, ReferenceCollection, ReferenceText
from app.conversation_contracts import ConversationSnapshot, GenerationPreferences, PendingTurn, TurnPlan
from app.conversation_service import ConversationService
from app.gemini_provider import GeminiProvider
from app.revision_generation import RevisionBatch
from fictional_samples import CASES, content_for


CONTENT = (
    {"title": "[FICTIONAL] Rain Garden Opens a Ceramic Canopy", "body": "In an invented village, a ceramic canopy gathers rain into bright spiral bowls. Neighbors arrange painted umbrellas around a quiet courtyard during the imaginary afternoon.", "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"},
    {"title": "[FICTIONAL] Forest Library Opens a Map Room", "body": "Across a make-believe forest, a library of wooden shelves unveils maps drawn on translucent leaves. Visitors follow glowing paths between rooms devoted to invented journeys.", "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"},
    {"title": "[FICTIONAL] Music Workshop Builds a Ribbon Orchestra", "body": "Beneath an imaginary bridge, a workshop transforms colorful ribbons into a silent orchestra. Each flowing strand follows a different rhythm through an invented rehearsal space.", "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"},
)


def provider_with(*responses: object) -> Mock:
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(side_effect=responses)
    return provider


def run_turn(provider: Mock, instruction: str, snapshot: ConversationSnapshot | None = None, **settings: object):
    return asyncio.run(ConversationService(provider).handle_turn(ChatTurnRequest(conversation_id=snapshot.conversation_id if snapshot else None, instruction=instruction, **settings), snapshot))


def prompt_data(provider: Mock, index: int) -> dict:
    text = provider.generate.call_args_list[index].args[0]
    return json.JSONDecoder().raw_decode(text.split("DATA_JSON:\n", 1)[1])[0]


@pytest.mark.parametrize("news_request", ["World of Warcraft Forever recent news", "latest announcements about robotics", "Recent news about Fantasy Life", "current patch updates for an online game"])
def test_news_topic_switch_generates_fiction_without_previous_topic(news_request: str) -> None:
    provider = provider_with(TurnPlan(action="generate"), DraftBatch(entries=[CONTENT[0]]))
    first = run_turn(provider, "create rainy day no:13")
    before = first.snapshot.model_dump_json()
    provider.generate.side_effect = [TurnPlan(action="message"), DraftBatch(entries=[content_for(CASES[-1], "update" if "updates" in news_request else "announcement" if "announcements" in news_request else "news")])]
    second = run_turn(provider, news_request, first.snapshot)
    assert second.response.kind == "generation" and len(second.response.entries) == 1
    assert second.snapshot.active_entries[:-1] == first.snapshot.active_entries
    assert second.snapshot.preferences.topic == news_request and second.snapshot.pending is None
    assert second.snapshot.messages[-2].content == news_request
    assert first.snapshot.model_dump_json() == before
    assert "rainy day no:13" not in provider.generate.call_args.args[0]
    assert provider.generate.await_count == 4


@pytest.mark.parametrize("case", CASES[:6], ids=[case[0] for case in CASES[:6]])
@pytest.mark.parametrize("action", ["generate", "message", "clarification"])
def test_ordinary_topics_create_mock_entries_without_verification(case: tuple[str, str, str, str], action: str) -> None:
    plan = TurnPlan(action=action, clarification_reason="unsupported_requirement" if action == "clarification" else None, question="Can you supply a verified official source?" if action == "clarification" else None)
    provider = provider_with(plan, DraftBatch(entries=[content_for(case)]))
    result = run_turn(provider, case[0], generation_settings={"category": case[1], "count": 1})
    assert result.response.kind == "generation" and len(result.response.entries) == 1
    entry = result.response.entries[0]
    assert entry.title == content_for(case)["title"] and entry.body == case[3]
    assert entry.category == case[1] and entry.is_synthetic is True and entry.status == "draft"
    assert entry.id is not None and entry.version == 1
    assert result.snapshot.preferences.topic == case[0]
    assert prompt_data(provider, 1)["constraints"]["mode"] == "synthetic"
    assert provider.generate.await_count == 2


def test_standalone_topics_do_not_receive_previous_messages_parameters_or_drafts() -> None:
    provider = provider_with()
    snapshot = None
    for index, instruction in enumerate(["rainy day no:13", "a forest library", "a ribbon orchestra"]):
        provider.generate.side_effect = [TurnPlan(action="generate", preferences=GenerationPreferences(topic="rainy day no:13", count=3)), DraftBatch(entries=[CONTENT[index]])]
        snapshot = run_turn(provider, instruction, snapshot).snapshot
        interpretation = prompt_data(provider, index * 2)["conversation"]
        generation = prompt_data(provider, index * 2 + 1)
        assert interpretation["preferences"]["topic"] is None
        assert interpretation["recent_messages"] == [] and interpretation["active_entries"] == []
        assert interpretation["pending"] is None
        assert generation["constraints"]["topic"] == instruction
        assert generation["constraints"]["count"] == 1
        text = provider.generate.call_args_list[index * 2 + 1].args[0]
        context = json.loads(text.split("CONVERSATION_JSON:\n", 1)[1])
        assert context["recent_messages"] == [] and context["active_entries"] == []
        assert context["current_instruction"] == instruction
        if index:
            assert "rainy day no:13" not in text
    assert len(snapshot.active_entries) == 3
    assert len(snapshot.messages) == 6


def test_unspecified_quantity_and_other_parameters_reset_for_a_new_topic() -> None:
    first_batch = DraftBatch(entries=[{**entry, "category": "news", "language": "tr"} for entry in CONTENT])
    provider = provider_with(TurnPlan(action="generate", preferences=GenerationPreferences(tone="playful", category="news", language="tr", min_body_chars=60, max_body_chars=500)), first_batch)
    first = run_turn(provider, "Create 3 fantasy stories in Turkish.", generation_settings={"category": "news"})
    assert first.response.kind == "generation" and len(first.response.entries) == 3
    provider.generate.side_effect = [TurnPlan(action="generate"), DraftBatch(entries=[{**CONTENT[1], "title": "[FICTIONAL] Cloud Observatory Opens a New Telescope", "body": "Inside a fictional cloud observatory, small glass telescopes reveal invented constellations above a painted horizon. Guests sketch unusual shapes during a quiet nighttime exhibition."}])]
    second = run_turn(provider, "a cloud observatory", first.snapshot)
    assert second.response.kind == "generation" and len(second.response.entries) == 1
    assert second.snapshot.preferences.model_dump() == {"topic": "a cloud observatory", "count": 1, "tone": "neutral", "category": "announcement", "language": "en", "min_body_chars": 100, "max_body_chars": 300}


def test_explicit_previous_article_reference_revises_only_the_latest_entry() -> None:
    entries = [GeneratedEntry(id=uuid4(), version=1, **entry) for entry in CONTENT[:2]]
    snapshot = ConversationSnapshot(conversation_id=uuid4(), active_entries=entries, preferences=GenerationPreferences(topic="forest library", count=2, tone="neutral", category="announcement", language="en", min_body_chars=100, max_body_chars=300))
    revised = {**CONTENT[1], "body": "In an imagined forest library, maps glow on translucent leaves. Readers trace short paths between shelves of wooden books."}
    provider = provider_with(TurnPlan(action="revise", length_change="shorter"), RevisionBatch(entries=[{"entry_id": entries[-1].id, **revised}]))
    result = run_turn(provider, "make the previous article shorter", snapshot)
    assert result.response.kind == "revision"
    assert result.response.entries[0].id == entries[-1].id and result.response.entries[0].version == 2
    assert result.snapshot.active_entries[0] == entries[0]
    assert prompt_data(provider, 0)["conversation"]["active_entries"]


def test_another_one_reuses_subject_but_not_previous_batch_size() -> None:
    snapshot = ConversationSnapshot(conversation_id=uuid4(), preferences=GenerationPreferences(topic="an imaginary forest", count=3, tone="neutral", category="announcement", language="en", min_body_chars=100, max_body_chars=300))
    provider = provider_with(TurnPlan(action="generate", preferences=GenerationPreferences(count=3)), DraftBatch(entries=[CONTENT[1]]))
    result = run_turn(provider, "create another one", snapshot)
    assert result.response.kind == "generation" and len(result.response.entries) == 1
    assert result.snapshot.preferences.topic == "an imaginary forest"
    assert prompt_data(provider, 0)["conversation"]["preferences"]["topic"] == "an imaginary forest"


def test_old_pending_instruction_and_standing_topic_rules_do_not_override_new_request() -> None:
    snapshot = ConversationSnapshot(
        conversation_id=uuid4(), preferences=GenerationPreferences(topic="rainy day no:13", count=3),
        pending=PendingTurn(action="generate", instruction="create rainy day no:13"),
        stable_instructions=["Always use rain as the subject."],
        references=ReferenceCollection(examples=[ReferenceText(id=uuid4(), text="A rain-only sample from an earlier request.")]),
    )
    provider = provider_with(TurnPlan(action="generate", preferences=GenerationPreferences(topic="rainy day no:13")), DraftBatch(entries=[CONTENT[2]]))
    result = run_turn(provider, "write about artificial intelligence", snapshot)
    assert result.response.kind == "generation"
    for call in provider.generate.call_args_list:
        assert "rain-only" not in call.args[0] and "rainy day no:13" not in call.args[0] and "Always use rain" not in call.args[0]
    assert result.snapshot.preferences.topic == "about artificial intelligence"
    assert result.snapshot.pending is None
    assert result.snapshot.references == snapshot.references
    assert result.snapshot.stable_instructions == snapshot.stable_instructions


@pytest.mark.parametrize("instruction", ["Create a fictional rainy-day news story", "Write a fantasy story about Azeroth", "Create fictional recent news about an imaginary village"])
def test_explicit_creative_requests_remain_available(instruction: str) -> None:
    category = "news" if "news" in instruction else "announcement"
    provider = provider_with(TurnPlan(action="generate"), DraftBatch(entries=[{**CONTENT[0], "category": category}]))
    result = run_turn(provider, instruction)
    assert result.response.kind == "generation"
    assert provider.generate.await_count == 2


@pytest.mark.parametrize("defect", ["count", "language", "category"])
def test_results_must_match_current_request_constraints(defect: str) -> None:
    entries = [{**CONTENT[0], "language": "fr", "category": "news"}]
    if defect == "count":
        entries.append({**CONTENT[1], "language": "fr", "category": "news"})
    else:
        entries[0][defect] = "en" if defect == "language" else "announcement"
    provider = provider_with(TurnPlan(action="generate", preferences=GenerationPreferences(language="fr", category="news")), DraftBatch(entries=entries))
    result = run_turn(provider, "Write one news draft in French about an imaginary forest.")
    assert result.response.kind == "error"
    assert result.response.error.code == "generation_failed"
    assert result.snapshot is None

@pytest.mark.parametrize("category", ["news", "announcement", "update", "article", "blog"])
def test_selected_type_survives_an_old_interpreter_category(category: str) -> None:
    provider = provider_with(TurnPlan(action="generate", preferences=GenerationPreferences(category="announcement")), DraftBatch(entries=[content_for(CASES[3], category)]))
    result = run_turn(provider, "New AI technology discovered", generation_settings={"category": category})
    assert result.response.kind == "generation"
    assert result.response.entries[0].category == category
    assert prompt_data(provider, 1)["constraints"]["category"] == category


@pytest.mark.parametrize("instruction", ["Hello", "Thanks", "Save these preferences.", "Remember this style.", "Keep titles short."])
def test_acknowledgements_and_preference_saving_do_not_generate(instruction: str) -> None:
    provider = provider_with(TurnPlan(action="message"))
    result = run_turn(provider, instruction)
    assert result.response.kind == "message"
    provider.generate.assert_awaited_once()

@pytest.mark.parametrize("category", ["update", "article", "blog"])
def test_supported_type_does_not_require_a_planner_to_approve_it(category: str) -> None:
    plan = TurnPlan(action="clarification", clarification_reason="unsupported_requirement", question="Which supported content type should I use? Only news and announcement are supported.")
    provider = provider_with(plan, DraftBatch(entries=[content_for(CASES[3], category)]))
    result = run_turn(provider, CASES[3][0], generation_settings={"category": category})
    assert result.response.kind == "generation"
    assert result.response.entries[0].category == category
