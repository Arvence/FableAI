import json
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from app.contracts import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_MESSAGES,
    MAX_ENTRIES,
    MAX_ENTRY_BODY_CHARS,
    MAX_HEADLINE_CHARS,
    MAX_INSTRUCTION_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_REFERENCE_CHARS,
    MAX_REFERENCES,
    MAX_TAG_CHARS,
    MAX_TAGS,
    MAX_TOTAL_REFERENCE_CHARS,
    MAX_URL_CHARS,
    MAX_URLS,
    ChatResponse,
    ChatTurnRequest,
    Conversation,
    ErrorResponse,
    GeneratedEntry,
    ReferenceCollection,
    URLReference,
)


CONVERSATION_ID = "5c7c0c86-4648-4c36-8179-ec06b67b9f16"
MESSAGE_ID = "87ddc8ca-8f70-4bb6-a50c-d426e5b0b293"
ENTRY_ID = "c39acf3a-075d-4c89-9e2f-1e401867bec6"
RESPONSE_ADAPTER = TypeAdapter(ChatResponse)


def entry_payload(version: int = 1) -> dict:
    return {
        "id": ENTRY_ID,
        "title": "Fictional sky garden opens",
        "body": "In this fictional announcement, a floating garden opens above an imaginary city.",
        "category": "announcement",
        "tags": ["fiction"],
        "language": "en",
        "is_synthetic": True,
        "status": "draft",
        "version": version,
    }


def response_payload(kind: str = "generation") -> dict:
    return {
        "conversation_id": CONVERSATION_ID,
        "message_id": MESSAGE_ID,
        "kind": kind,
        "assistant_message": "Which language should the fictional draft use?" if kind == "clarification" else "The fictional draft is ready for review.",
        "entries": [entry_payload(2 if kind == "revision" else 1)] if kind in {"generation", "revision"} else [],
    }


def message_payload(content: str = "Create a fictional announcement.", role: str = "user") -> dict:
    return {"conversation_id": CONVERSATION_ID, "message_id": str(uuid4()), "role": role, "content": content}


def test_illustrative_request_json() -> None:
    request = ChatTurnRequest.model_validate_json('''{
        "instruction": "Write one explicitly fictional announcement in English.",
        "examples": ["An imaginary library welcomed its first cloud reader."],
        "headlines": ["A fictional library opens above the clouds"],
        "source_urls": ["https://example.com/reference"],
        "import_urls": false,
        "generation_count": 1
    }''')

    assert request.conversation_id is None
    assert request.generation_count == 1
    assert request.import_urls is False
    assert str(request.source_urls[0]) == "https://example.com/reference"
    assert ChatTurnRequest.model_validate_json(request.model_dump_json()) == request


def test_minimal_turn_and_explicit_import_intent() -> None:
    request = ChatTurnRequest(instruction="  Use an imaginary setting.  ")
    assert request.instruction == "Use an imaginary setting."
    assert request.examples == request.headlines == request.source_urls == []
    assert request.import_urls is False
    assert request.generation_count is None
    imported = ChatTurnRequest(instruction="Use this authorized public sample.", source_urls=["https://example.com/sample"], import_urls=True)
    assert imported.import_urls is True


@pytest.mark.parametrize("instruction", ["", " \n\t ", None, 123, True, [], {}])
def test_rejects_empty_or_non_string_instructions(instruction: object) -> None:
    with pytest.raises(ValidationError):
        ChatTurnRequest.model_validate({"instruction": instruction})


@pytest.mark.parametrize("changes", [{}, {"instruction": "Use this.", "unknown": True}])
def test_rejects_missing_instruction_and_unknown_fields(changes: dict) -> None:
    with pytest.raises(ValidationError):
        ChatTurnRequest.model_validate(changes)


@pytest.mark.parametrize("field,value", [
    ("examples", "sample"), ("examples", [123]), ("examples", [" "]),
    ("headlines", [None]), ("headlines", [" "]), ("source_urls", "https://example.com"),
    ("import_urls", "true"), ("import_urls", 1), ("conversation_id", "not-a-uuid"),
])
def test_rejects_wrong_turn_field_types(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ChatTurnRequest.model_validate({"instruction": "Use this sample.", field: value})


@pytest.mark.parametrize("url", ["", "example.com", "not a URL", "ftp://example.com", "javascript:alert(1)", "https://", 123])
def test_rejects_invalid_links(url: object) -> None:
    with pytest.raises(ValidationError):
        ChatTurnRequest.model_validate({"instruction": "Use this reference.", "source_urls": [url]})


@pytest.mark.parametrize("count", [-1, 0, MAX_ENTRIES + 1, "2", True, 1.5])
def test_rejects_out_of_range_or_coerced_generation_counts(count: object) -> None:
    with pytest.raises(ValidationError):
        ChatTurnRequest.model_validate({"instruction": "Create fictional entries.", "generation_count": count})


@pytest.mark.parametrize("count", [1, MAX_ENTRIES])
def test_accepts_generation_count_boundaries(count: int) -> None:
    assert ChatTurnRequest(instruction="Create fictional entries.", generation_count=count).generation_count == count


@pytest.mark.parametrize("field,limit", [("instruction", MAX_INSTRUCTION_CHARS), ("examples", MAX_REFERENCE_CHARS), ("headlines", MAX_HEADLINE_CHARS)])
def test_turn_text_length_boundaries(field: str, limit: int) -> None:
    payload = {"instruction": "Create a fictional draft.", field: "a" * limit if field == "instruction" else ["a" * limit]}
    ChatTurnRequest.model_validate(payload)
    payload[field] = "a" * (limit + 1) if field == "instruction" else ["a" * (limit + 1)]
    with pytest.raises(ValidationError):
        ChatTurnRequest.model_validate(payload)


def test_reference_count_and_total_character_budgets() -> None:
    ChatTurnRequest(instruction="Use these samples.", examples=["a"] * MAX_REFERENCES)
    with pytest.raises(ValidationError):
        ChatTurnRequest(instruction="Use these samples.", examples=["a"] * MAX_REFERENCES, headlines=["Headline"])
    examples = ["a" * MAX_REFERENCE_CHARS] * (MAX_TOTAL_REFERENCE_CHARS // MAX_REFERENCE_CHARS)
    ChatTurnRequest(instruction="Use these samples.", examples=examples)
    with pytest.raises(ValidationError):
        ChatTurnRequest(instruction="Use these samples.", examples=examples, headlines=["a"])


def test_url_count_and_length_boundaries() -> None:
    ChatTurnRequest(instruction="Keep these links.", source_urls=[f"https://example.com/{index}" for index in range(MAX_URLS)])
    with pytest.raises(ValidationError):
        ChatTurnRequest(instruction="Keep these links.", source_urls=[f"https://example.com/{index}" for index in range(MAX_URLS + 1)])
    prefix = "https://example.com/"
    url = prefix + "a" * (MAX_URL_CHARS - len(prefix))
    ChatTurnRequest(instruction="Keep this link.", source_urls=[url])
    with pytest.raises(ValidationError):
        ChatTurnRequest(instruction="Keep this link.", source_urls=[url + "a"])


def test_link_only_reference_does_not_claim_import() -> None:
    reference = URLReference(id=str(uuid4()), url="https://example.com/sample")
    payload = json.loads(reference.model_dump_json())
    assert payload["import_status"] == "not_requested"
    assert payload["imported_text"] is None
    assert payload["error"] is None
    with pytest.raises(ValidationError):
        ChatTurnRequest(instruction="Import a reference.", import_urls=True)


@pytest.mark.parametrize("status,extra", [
    ("not_requested", {}), ("pending", {}),
    ("imported", {"imported_text": "Authorized fictional sample."}),
    ("failed", {"error": {"code": "reference_import_failed", "message": "The reference could not be imported."}}),
])
def test_valid_mocked_import_states(status: str, extra: dict) -> None:
    reference = URLReference.model_validate({"id": str(uuid4()), "url": "https://example.com/sample", "import_status": status, **extra})
    assert URLReference.model_validate_json(reference.model_dump_json()) == reference


@pytest.mark.parametrize("status,extra", [
    ("imported", {}), ("imported", {"imported_text": " "}), ("failed", {}),
    ("not_requested", {"imported_text": "Not actually retrieved."}),
    ("pending", {"imported_text": "Not actually retrieved."}),
    ("pending", {"error": {"code": "reference_import_failed", "message": "Failed."}}),
    ("failed", {"error": {"code": "generation_failed", "message": "Wrong error type."}}),
    ("failed", {"imported_text": "Unexpected text.", "error": {"code": "reference_import_failed", "message": "Failed."}}),
    ("imported", {"imported_text": "Sample.", "error": {"code": "reference_import_failed", "message": "Failed."}}),
    ("unknown", {}),
])
def test_rejects_inconsistent_import_states(status: str, extra: dict) -> None:
    with pytest.raises(ValidationError):
        URLReference.model_validate({"id": str(uuid4()), "url": "https://example.com/sample", "import_status": status, **extra})


def test_reference_collection_identity_and_budgets() -> None:
    reference_id = str(uuid4())
    references = {"examples": [{"id": reference_id, "text": "Fictional source."}], "headlines": [{"id": str(uuid4()), "text": "Sample headline"}]}
    collection = ReferenceCollection.model_validate(references)
    assert ReferenceCollection.model_validate_json(collection.model_dump_json()) == collection
    references["headlines"][0]["id"] = reference_id
    with pytest.raises(ValidationError):
        ReferenceCollection.model_validate(references)
    with pytest.raises(ValidationError):
        ReferenceCollection(examples=[{"id": str(uuid4()), "text": "a"} for _ in range(MAX_REFERENCES)], urls=[{"id": str(uuid4()), "url": "https://example.com"}])
    with pytest.raises(ValidationError):
        ReferenceCollection(urls=[{"id": str(uuid4()), "url": "https://example.com", "import_status": "imported", "imported_text": "a" * MAX_REFERENCE_CHARS} for _ in range(3)])


@pytest.mark.parametrize("kind", ["message", "clarification", "generation", "revision"])
def test_mocked_response_json_round_trip(kind: str) -> None:
    payload = response_payload(kind)
    response = RESPONSE_ADAPTER.validate_json(json.dumps(payload))
    assert json.loads(response.model_dump_json()) == payload
    assert response.kind == kind


@pytest.mark.parametrize("field", ["conversation_id", "message_id", "kind", "assistant_message", "entries"])
def test_response_requires_shared_fields(field: str) -> None:
    payload = response_payload("clarification")
    del payload[field]
    with pytest.raises(ValidationError):
        RESPONSE_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize("kind,entries", [
    ("message", [entry_payload()]), ("clarification", [entry_payload()]),
    ("generation", []), ("revision", []),
    ("generation", [entry_payload(2)]), ("revision", [entry_payload(1)]),
    ("unknown", []),
])
def test_rejects_incompatible_response_kinds(kind: str, entries: list) -> None:
    payload = {**response_payload(), "kind": kind, "entries": entries}
    with pytest.raises(ValidationError):
        RESPONSE_ADAPTER.validate_json(json.dumps(payload))


@pytest.mark.parametrize("raw", ["Which language?", '"Which language?"', "null", "[]"])
def test_rejects_unstructured_model_output(raw: str) -> None:
    with pytest.raises(ValidationError):
        RESPONSE_ADAPTER.validate_json(raw)


@pytest.mark.parametrize("field,value", [
    ("id", "invalid"), ("title", " "), ("body", ""), ("body", 123),
    ("category", "product"), ("tags", "fiction"), ("tags", [""]), ("tags", [123]),
    ("language", ""), ("language", "en_US"), ("language", 123),
    ("is_synthetic", False), ("is_synthetic", 1), ("is_synthetic", "true"),
    ("status", "published"), ("version", 0), ("version", -1), ("version", "1"), ("version", True),
    ("author", "Unrequested field"),
])
def test_rejects_invalid_generated_entries(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        GeneratedEntry.model_validate_json(json.dumps({**entry_payload(), field: value}))


def test_entry_text_and_tag_boundaries() -> None:
    payload = {
        **entry_payload(),
        "title": "a" * MAX_HEADLINE_CHARS,
        "body": "a" * MAX_ENTRY_BODY_CHARS,
        "tags": ["a" * MAX_TAG_CHARS] * MAX_TAGS,
        "language": "en-US",
    }
    GeneratedEntry.model_validate(payload)
    invalid_values = (
        ("title", "a" * (MAX_HEADLINE_CHARS + 1)),
        ("body", "a" * (MAX_ENTRY_BODY_CHARS + 1)),
        ("tags", ["a"] * (MAX_TAGS + 1)),
        ("tags", ["a" * (MAX_TAG_CHARS + 1)]),
    )
    for field, value in invalid_values:
        with pytest.raises(ValidationError):
            GeneratedEntry.model_validate({**payload, field: value})


def test_response_entry_count_and_unique_ids() -> None:
    entries = [{**entry_payload(), "id": str(uuid4())} for _ in range(MAX_ENTRIES)]
    RESPONSE_ADAPTER.validate_python({**response_payload(), "entries": entries})
    with pytest.raises(ValidationError):
        RESPONSE_ADAPTER.validate_python({**response_payload(), "entries": entries + [entry_payload()]})
    with pytest.raises(ValidationError):
        RESPONSE_ADAPTER.validate_python({**response_payload(), "entries": [entry_payload(), entry_payload()]})


def test_targeted_revision_json_preserves_identity_and_increments_version() -> None:
    request = ChatTurnRequest.model_validate_json(json.dumps({
        "conversation_id": CONVERSATION_ID,
        "instruction": "Shorten the selected fictional draft.",
        "revision_targets": [{"entry_id": ENTRY_ID, "expected_version": 2}],
    }))
    response = RESPONSE_ADAPTER.validate_json(json.dumps({**response_payload("revision"), "entries": [entry_payload(3)]}))
    assert response.entries[0].id == request.revision_targets[0].entry_id
    assert response.entries[0].version == request.revision_targets[0].expected_version + 1


@pytest.mark.parametrize("changes", [
    {"conversation_id": None}, {"generation_count": 1},
    {"revision_targets": [{"entry_id": ENTRY_ID, "expected_version": 0}]},
    {"revision_targets": [{"entry_id": ENTRY_ID, "expected_version": "1"}]},
    {"revision_targets": [{"entry_id": ENTRY_ID, "expected_version": 1}] * 2},
    {"revision_targets": [{"entry_id": str(uuid4()), "expected_version": 1} for _ in range(MAX_ENTRIES + 1)]},
])
def test_rejects_invalid_revision_requests(changes: dict) -> None:
    payload = {"conversation_id": CONVERSATION_ID, "instruction": "Revise this draft.", "revision_targets": [{"entry_id": ENTRY_ID, "expected_version": 1}], **changes}
    with pytest.raises(ValidationError):
        ChatTurnRequest.model_validate(payload)


def test_conversation_contains_typed_user_and_assistant_messages() -> None:
    conversation = Conversation(conversation_id=CONVERSATION_ID, messages=[message_payload(), message_payload("Which fictional setting should I use?", "assistant")])
    assert conversation.conversation_id == UUID(CONVERSATION_ID)
    assert Conversation.model_validate_json(conversation.model_dump_json()) == conversation
    assert [message.role for message in conversation.messages] == ["user", "assistant"]


def test_conversation_message_count_and_character_budgets() -> None:
    Conversation(conversation_id=CONVERSATION_ID, messages=[message_payload() for _ in range(MAX_CONTEXT_MESSAGES)])
    with pytest.raises(ValidationError):
        Conversation(conversation_id=CONVERSATION_ID, messages=[message_payload() for _ in range(MAX_CONTEXT_MESSAGES + 1)])
    messages = [message_payload("a" * MAX_MESSAGE_CHARS) for _ in range(MAX_CONTEXT_CHARS // MAX_MESSAGE_CHARS)]
    Conversation(conversation_id=CONVERSATION_ID, messages=messages)
    with pytest.raises(ValidationError):
        Conversation(conversation_id=CONVERSATION_ID, messages=messages + [message_payload("a")])
    with pytest.raises(ValidationError):
        Conversation(conversation_id=CONVERSATION_ID, messages=messages, references={"headlines": [{"id": str(uuid4()), "text": "a"}]})
    with pytest.raises(ValidationError):
        Conversation(conversation_id=CONVERSATION_ID, messages=[message_payload("a" * (MAX_MESSAGE_CHARS + 1))])


@pytest.mark.parametrize("change", [{"conversation_id": str(uuid4())}, {"role": "system"}, {"content": " "}, {"message_id": "invalid"}])
def test_rejects_invalid_conversation_messages(change: dict) -> None:
    with pytest.raises(ValidationError):
        Conversation(conversation_id=CONVERSATION_ID, messages=[{**message_payload(), **change}])


def test_rejects_duplicate_message_ids() -> None:
    message = message_payload()
    with pytest.raises(ValidationError):
        Conversation(conversation_id=CONVERSATION_ID, messages=[message, message])


def test_illustrative_error_json() -> None:
    response = ErrorResponse.model_validate_json('''{
        "kind": "error",
        "conversation_id": null,
        "message_id": null,
        "error": {
            "code": "invalid_request",
            "message": "The chat turn contains an invalid value.",
            "details": [{"field": "generation_count", "message": "Choose a count between 1 and 20."}]
        }
    }''')
    assert response.error.code == "invalid_request"
    assert ErrorResponse.model_validate_json(response.model_dump_json()) == response


@pytest.mark.parametrize("error", [
    "Something failed", {}, {"code": "unknown", "message": "Failed."},
    {"code": "invalid_request", "message": " "},
    {"code": "invalid_request", "message": "Failed.", "details": [{"field": "", "message": "Invalid."}]},
    {"code": "invalid_request", "message": "Failed.", "details": [{"field": "instruction", "message": "Invalid.", "input": "Raw input is not part of the error contract."}]},
])
def test_rejects_malformed_structured_errors(error: object) -> None:
    with pytest.raises(ValidationError):
        ErrorResponse.model_validate({"kind": "error", "error": error})


def test_response_schema_exposes_four_discriminated_variants() -> None:
    schema = RESPONSE_ADAPTER.json_schema()
    assert schema["discriminator"]["propertyName"] == "kind"
    assert set(schema["discriminator"]["mapping"]) == {"message", "clarification", "generation", "revision"}
    assert len(schema["oneOf"]) == 4
    for reference in schema["discriminator"]["mapping"].values():
        variant = schema["$defs"][reference.rsplit("/", 1)[1]]
        assert {"conversation_id", "message_id", "kind", "assistant_message", "entries"} <= set(variant["required"])


@pytest.mark.parametrize("settings", [
    {"count": 0}, {"count": MAX_ENTRIES + 1}, {"count": True}, {"count": "2"},
    {"body_length_mode": "unknown"}, {"body_length_mode": True}, {"tone": "sarcastic"}, {"category": "report"}, {"language": "English"},
    {"min_body_chars": 0}, {"max_body_chars": 6001},
    {"min_body_chars": 300, "max_body_chars": 100},
    {"count": 20, "max_body_chars": 1200}, {"topic": "unexpected"},
])
def test_invalid_selected_generation_settings_are_rejected(settings: dict) -> None:
    with pytest.raises(ValidationError):
        ChatTurnRequest(instruction="A rainy afternoon", generation_settings=settings)


def test_generation_settings_are_optional_and_do_not_conflict_with_revision_targets() -> None:
    assert ChatTurnRequest(instruction="A rainy afternoon").generation_settings is None
    request = ChatTurnRequest(conversation_id=CONVERSATION_ID, instruction="Shorten entry 1.", generation_settings={"count": 10, "category": "news"}, revision_targets=[{"entry_id": ENTRY_ID, "expected_version": 1}])
    assert request.generation_settings.count == 10
    assert ChatTurnRequest.model_validate_json(request.model_dump_json()) == request
