import asyncio
import json
from collections.abc import Callable
from copy import deepcopy

import httpx
import pytest
from google import genai
from pydantic import TypeAdapter

from app.config import Settings
from app.contracts import DraftBatch
from app.conversation_contracts import ConversationResponse
from app.gemini_provider import GeminiProvider
from app.synthetic_generation import SyntheticGenerationService
from quality_evaluation import BY_ID, CASES, CHECKS, DATASET, MANUAL_REVIEW_QUESTIONS, evaluate_candidate, list_item_count, make_references, make_request, paragraph_count


@pytest.fixture
def offline_generation(monkeypatch: pytest.MonkeyPatch) -> Callable:
    monkeypatch.setattr("google.genai._api_client.get_env_api_key", lambda: None)

    def build(raw_json: str) -> tuple[SyntheticGenerationService, list[httpx.Request]]:
        requests = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": raw_json}]}, "finishReason": "STOP"}]})

        def client_factory(**kwargs: object) -> genai.Client:
            kwargs["http_options"].async_client_args = {"transport": httpx.MockTransport(handle), "trust_env": False}
            kwargs["http_options"].client_args = {"trust_env": False}
            return genai.Client(**kwargs)

        provider = GeminiProvider(Settings(gemini_api_key="fake-offline-quality-key", gemini_model="mock-quality-model"), client_factory=client_factory)
        return SyntheticGenerationService(provider), requests

    return build


def run_candidate(factory: Callable, case: dict, payload: dict | str, **constraints: object) -> tuple:
    raw_json = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    service, requests = factory(raw_json)
    request = make_request(case, **constraints)
    response = asyncio.run(service.generate(request, make_references(case)))
    assert TypeAdapter(ConversationResponse).validate_json(response.model_dump_json()) == response
    assert len(requests) == 1
    return response, evaluate_candidate(raw_json, case, request), requests[0]


def test_corpus_is_original_varied_and_has_explicit_offline_provenance() -> None:
    assert "Self-authored" in DATASET["provenance"] and "hand-authored mock outputs" in DATASET["provenance"]
    assert len(CASES) >= 15 and len(BY_ID) == len(CASES)
    assert len({case["reference"] for case in CASES}) == len(CASES)
    assert {case["constraints"]["language"] for case in CASES} == {"en", "tr", "es", "de", "fr"}
    assert {case["constraints"]["tone"] for case in CASES} == {"neutral", "formal", "informative", "playful"}
    assert {case["format"] for case in CASES} == {"paragraph", "paragraphs", "bullets", "numbered"}
    assert {case["constraints"]["category"] for case in CASES} == {"news", "announcement"}
    lengths = [len(case["reference"]) for case in CASES]
    assert min(lengths) < 120 and max(lengths) > 300
    assert set(MANUAL_REVIEW_QUESTIONS) == {"semantic_topic_fit", "style_and_fluency", "actual_language", "semantic_diversity", "paraphrased_copying", "real_world_attribution"}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_quality_matrix_through_real_sdk_with_mock_transport(offline_generation: Callable, case: dict) -> None:
    response, evaluation, wire_request = run_candidate(offline_generation, case, {"entries": [case["candidate"]]})

    assert response.kind == "generation", response.model_dump()
    assert set(evaluation.checks) == set(CHECKS)
    assert all(value is True for key, value in evaluation.checks.items() if key != "lexical_diversity_proxy"), evaluation
    assert evaluation.checks["lexical_diversity_proxy"] is None
    assert set(evaluation.manual_review.values()) == {"not_evaluated"}
    assert response.entries[0].model_dump(exclude={"id", "version"}) == case["candidate"]
    assert evaluation.measurements["maximum_reference_shared_word_run"] < 8
    assert evaluation.measurements["maximum_reference_word_jaccard"] < 0.8

    wire_body = json.loads(wire_request.content)
    assert wire_body["generationConfig"]["responseJsonSchema"] == DraftBatch.model_json_schema()
    prompt = wire_body["contents"][0]["parts"][0]["text"]
    data = json.loads(prompt.split("DATA_JSON:\n", 1)[1])
    assert data["constraints"] == make_request(case).model_dump(mode="json", exclude={"conversation_id"})
    sample = data["reference_patterns"]["samples"][0]
    assert sample["source_characters"] == len(case["reference"])
    assert sample["paragraph_count"] == paragraph_count(case["reference"]) == case["expected_paragraphs"]
    assert sample["bullet_line_count"] == list_item_count(case["reference"]) == case["expected_list_items"]
    assert case["reference"] not in prompt
    assert "fake-offline-quality-key" not in response.model_dump_json()


@pytest.mark.parametrize(("defect", "failed_check", "message_fragment"), [
    ("malformed_json", "json_schema", "invalid structured response"),
    ("false_synthetic_flag", "json_schema", "invalid structured response"),
    ("invented_extra_field", "json_schema", "invalid structured response"),
    ("missing_label", "fictional_label", "[FICTIONAL]"),
    ("wrong_count", "requested_count", "count"),
    ("too_short", "body_length", "character range"),
    ("too_long", "body_length", "character range"),
    ("wrong_language_metadata", "category_language_metadata", "metadata"),
    ("wrong_category", "category_language_metadata", "metadata"),
    ("copied_reference", "sample_overlap", "overlaps"),
    ("normalized_copy", "sample_overlap", "overlaps"),
    ("duplicate_entries", "lexical_diversity_proxy", "overlaps"),
    ("unsupported_claim", None, "verification or endorsement"),
    ("formal_tone_conflict", None, "tone"),
])
def test_defect_matrix_rejects_invalid_outputs_without_retry(offline_generation: Callable, defect: str, failed_check: str | None, message_fragment: str) -> None:
    case = BY_ID["en-brief"]
    payload = {"entries": [deepcopy(case["candidate"])]}
    entry = payload["entries"][0]
    constraints = {}
    if defect == "malformed_json":
        payload = '{"entries":'
    elif defect == "false_synthetic_flag":
        entry["is_synthetic"] = False
    elif defect == "invented_extra_field":
        entry["publication_url"] = "https://fixture.invalid"
    elif defect == "missing_label":
        entry["title"] = entry["title"].removeprefix("[FICTIONAL] ")
    elif defect == "wrong_count":
        payload["entries"].append(deepcopy(entry))
    elif defect == "too_short":
        entry["body"] = "Tiny."
    elif defect == "too_long":
        entry["body"] = "x" * (case["constraints"]["max_body_chars"] + 1)
    elif defect == "wrong_language_metadata":
        entry["language"] = "fr"
    elif defect == "wrong_category":
        entry["category"] = "announcement"
    elif defect in {"copied_reference", "normalized_copy"}:
        entry["body"] = case["reference"]
        if defect == "normalized_copy":
            entry["body"] = "".join(chr(ord(char) + 0xFEE0) if "!" <= char <= "~" else char for char in entry["body"])
    elif defect == "duplicate_entries":
        payload["entries"].append(deepcopy(entry))
        constraints["count"] = 2
    elif defect == "unsupported_claim":
        entry["body"] = "The imaginary textile exhibit is officially verified by real authorities and endorsed as an actual event."
    else:
        entry["body"] += " Awesome!"
        constraints["tone"] = "formal"

    response, evaluation, _ = run_candidate(offline_generation, case, payload, **constraints)

    assert response.kind == "error" and response.error.code == "generation_failed"
    assert message_fragment in response.error.message
    assert "entries" not in response.model_dump()
    if failed_check:
        assert evaluation.checks[failed_check] is False
    else:
        assert evaluation.checks["json_schema"] is True
    if evaluation.checks["json_schema"] is False:
        assert all(value is None for key, value in evaluation.checks.items() if key != "json_schema")


@pytest.mark.parametrize("boundary", ["minimum", "maximum"])
def test_multibyte_body_lengths_are_inclusive_character_counts(offline_generation: Callable, boundary: str) -> None:
    case = BY_ID["tr-brief"]
    length = len(case["candidate"]["body"])
    assert len(case["candidate"]["body"].encode("utf-8")) > length
    constraints = {"min_body_chars": length} if boundary == "minimum" else {"max_body_chars": length}
    accepted, evaluation, _ = run_candidate(offline_generation, case, {"entries": [case["candidate"]]}, **constraints)
    assert accepted.kind == "generation" and evaluation.checks["body_length"] is True

    constraints = {"min_body_chars": length + 1} if boundary == "minimum" else {"max_body_chars": length - 1}
    rejected, evaluation, _ = run_candidate(offline_generation, case, {"entries": [case["candidate"]]}, **constraints)
    assert rejected.kind == "error" and evaluation.checks["body_length"] is False


@pytest.mark.parametrize(("case_id", "defect", "failed_check"), [
    ("en-brief", "off_topic", "topic_term_proxy"),
    ("en-bullets", "flattened_list", "format_proxy"),
])
def test_evaluation_proxies_flag_quality_failures_that_valid_json_misses(offline_generation: Callable, case_id: str, defect: str, failed_check: str) -> None:
    case = BY_ID[case_id]
    entry = deepcopy(case["candidate"])
    if defect == "off_topic":
        entry["title"] = "[FICTIONAL] Quiet Garden Arranges Pebbles"
        entry["body"] = "A fabricated garden in Mistford places smooth stones beside a shallow pool. Visitors arrange small paths around painted reeds."
    else:
        entry["body"] = entry["body"].replace("\n- ", " ")

    response, evaluation, _ = run_candidate(offline_generation, case, {"entries": [entry]})

    assert evaluation.checks["json_schema"] is True
    assert evaluation.checks[failed_check] is False
    print(f"Quality counterexample {defect}: backend={response.kind}; failed_check={failed_check}")
    assert response.kind in {"generation", "error"}
    if response.kind == "generation":
        assert response.entries[0].body == entry["body"]
    else:
        assert response.error.code == "generation_failed"


@pytest.mark.parametrize("limitation", ["wrong_body_language", "unquoted_attribution", "paraphrased_reference", "semantic_repetition"])
def test_lexical_checks_leave_semantic_quality_explicitly_unevaluated(offline_generation: Callable, limitation: str) -> None:
    case = deepcopy(BY_ID["tr-brief" if limitation == "wrong_body_language" else "en-brief"])
    entry = deepcopy(case["candidate"])
    payload = {"entries": [entry]}
    constraints = {}
    if limitation == "wrong_body_language":
        entry["body"] = BY_ID["en-brief"]["candidate"]["body"]
        review = "actual_language"
    elif limitation == "unquoted_attribution":
        entry["body"] = "UNESCO approved the textile exhibition in imaginary Vellum Bay. Its real staff designed the display for public release."
        review = "real_world_attribution"
    elif limitation == "paraphrased_reference":
        case["constraints"]["topic"] = "An imaginary pottery display"
        case["topic_terms"] = ["pottery"]
        entry["title"] = "[FICTIONAL] Pottery Booth Closes at Sundown"
        entry["body"] = "In the invented village of Morrowfen, a pottery booth presents small azure mugs. The showing ends at sunset."
        review = "paraphrased_copying"
    else:
        other = {**entry, "title": "[FICTIONAL] Woven Gallery Suspends Soft Braids", "body": "Floating fabric strips fill a fictional textile gallery above work surfaces. Guests reshape the hanging braids into loops with gentle movements."}
        payload["entries"].append(other)
        constraints["count"] = 2
        review = "semantic_diversity"

    response, evaluation, _ = run_candidate(offline_generation, case, payload, **constraints)

    assert all(value is True for key, value in evaluation.checks.items() if key != "lexical_diversity_proxy"), evaluation
    assert evaluation.checks["lexical_diversity_proxy"] is (True if len(payload["entries"]) > 1 else None)
    assert evaluation.manual_review[review] == "not_evaluated"
    print(f"Quality counterexample {limitation}: backend={response.kind}; manual_review_required={review}")
    assert "quality_score" not in evaluation.measurements
    assert response.kind in {"generation", "error"}
    if response.kind == "error":
        assert response.error.code == "generation_failed"


def test_distinct_multi_entry_fixture_has_measurable_lexical_diversity(offline_generation: Callable) -> None:
    case = BY_ID["en-brief"]
    other = {**case["candidate"], "title": "[FICTIONAL] Textile Workshop Tests a Color Wheel", "body": "At a pretend textile workshop in Amberfold, volunteers dye fabric using a rotating stencil. The fictional activity compares orange circles with green squares."}
    response, evaluation, _ = run_candidate(offline_generation, case, {"entries": [case["candidate"], other]}, count=2)

    assert response.kind == "generation"
    assert all(value is True for value in evaluation.checks.values()), evaluation
    assert 0 <= evaluation.measurements["maximum_pair_word_jaccard"] < 0.8
    assert evaluation.manual_review["semantic_diversity"] == "not_evaluated"
