import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from app.contracts import DraftBatch, GenerationRequest, ReferenceCollection, ReferenceText
from app.generation_checks import has_suspect_overlap, normalized_words


DATASET = json.loads((Path(__file__).parent / "fixtures" / "quality_cases.json").read_text(encoding="utf-8"))
CASES = DATASET["cases"]
BY_ID = {case["id"]: case for case in CASES}
CONVERSATION = DATASET["conversation"]
CONVERSATION_ID = UUID("5c7c0c86-4648-4c36-8179-ec06b67b9f16")
REFERENCE_ID = UUID("a39acf3a-075d-4c89-9e2f-1e401867bec6")
MAX_PAIR_WORD_JACCARD = 0.8
CHECKS = ("json_schema", "fictional_label", "requested_count", "body_length", "category_language_metadata", "topic_term_proxy", "format_proxy", "lexical_diversity_proxy", "sample_overlap")
MANUAL_REVIEW_QUESTIONS = {
    "semantic_topic_fit": "Does the draft address the intended fictional subject beyond matching keywords?",
    "style_and_fluency": "Does the prose naturally match the requested tone and reference structure?",
    "actual_language": "Is the body written fluently in the requested language rather than merely labeled with its code?",
    "semantic_diversity": "Do entries describe meaningfully different subjects rather than paraphrases of one event?",
    "paraphrased_copying": "Does the draft reuse a reference's distinctive story or expression despite low lexical overlap?",
    "real_world_attribution": "Does any wording imply a real event or real person's or organization's endorsement?",
}


@dataclass(frozen=True)
class QualityEvaluation:
    checks: dict[str, bool | None]
    measurements: dict
    manual_review: dict[str, str]


def make_request(case: dict, **changes: object) -> GenerationRequest:
    return GenerationRequest(conversation_id=CONVERSATION_ID, **{**case["constraints"], **changes})


def make_references(case: dict) -> ReferenceCollection:
    return ReferenceCollection(examples=[ReferenceText(id=REFERENCE_ID, text=case["reference"])])


def paragraph_count(text: str) -> int:
    return len([part for part in re.split(r"\n\s*\n", text) if part.strip()])


def list_item_count(text: str) -> int:
    return sum(bool(re.match(r"\s*(?:[-*]|\d+[.)])\s+", line)) for line in text.splitlines())


def word_jaccard(first: str, second: str) -> float:
    first_words, second_words = set(normalized_words(first)), set(normalized_words(second))
    union = first_words | second_words
    return len(first_words & second_words) / len(union) if union else 0.0


def shared_word_run(first: str, second: str) -> int:
    first_words, second_words = normalized_words(first), normalized_words(second)
    return SequenceMatcher(None, first_words, second_words, autojunk=False).find_longest_match().size


def evaluate_candidate(raw_json: str, case: dict, request: GenerationRequest | None = None) -> QualityEvaluation:
    checks = dict.fromkeys(CHECKS)
    manual_review = dict.fromkeys(MANUAL_REVIEW_QUESTIONS, "not_evaluated")
    try:
        batch = DraftBatch.model_validate_json(raw_json)
    except ValidationError:
        checks["json_schema"] = False
        return QualityEvaluation(checks, {}, manual_review)
    request = request or make_request(case)
    entries = batch.entries
    bodies = [entry.body for entry in entries]
    texts = [text for entry in entries for text in (entry.title, entry.body)]
    lengths = [len(body) for body in bodies]
    paragraphs = [paragraph_count(body) for body in bodies]
    list_items = [list_item_count(body) for body in bodies]
    topic_hits = [sum(set(normalized_words(term)) <= set(normalized_words(entry.title + " " + entry.body)) for term in case["topic_terms"]) for entry in entries]
    maximum_pair_similarity = max((word_jaccard(first, second) for first, second in combinations(bodies, 2)), default=0.0)
    checks.update({
        "json_schema": True,
        "fictional_label": all(entry.title.startswith("[FICTIONAL] ") and bool(normalized_words(entry.title)) and entry.is_synthetic is True for entry in entries),
        "requested_count": len(entries) == request.count,
        "body_length": all(request.min_body_chars <= length <= request.max_body_chars for length in lengths),
        "category_language_metadata": all(entry.category == request.category and entry.language.casefold() == request.language.casefold() for entry in entries),
        "topic_term_proxy": all(hits == len(case["topic_terms"]) for hits in topic_hits),
        "format_proxy": all(count == case["expected_paragraphs"] for count in paragraphs) and all(count == case["expected_list_items"] for count in list_items),
        "lexical_diversity_proxy": len({normalized_words(entry.title) for entry in entries}) == len(entries) and maximum_pair_similarity < MAX_PAIR_WORD_JACCARD if len(entries) > 1 else None,
        "sample_overlap": not any(has_suspect_overlap(text, case["reference"]) for text in texts),
    })
    measurements = {
        "body_characters": lengths,
        "paragraph_counts": paragraphs,
        "list_item_counts": list_items,
        "topic_term_hits": topic_hits,
        "maximum_pair_word_jaccard": maximum_pair_similarity if len(entries) > 1 else None,
        "maximum_reference_word_jaccard": max(word_jaccard(text, case["reference"]) for text in texts),
        "maximum_reference_shared_word_run": max(shared_word_run(text, case["reference"]) for text in texts),
    }
    return QualityEvaluation(checks, measurements, manual_review)
