import asyncio
import json
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.contracts import MAX_ENTRIES, DraftBatch, ErrorResponse
from app.conversation_contracts import GenerationPreferences, TurnPlan
from app.conversation_repository import PROCESSING_LEASE_SECONDS
from app.gemini_provider import GeminiProvider, GeminiProviderError, ProviderFailure
from app.main import create_app
from app.revision_generation import RevisionBatch
from draft_samples import rainy_day_batch
from fictional_samples import CASES, content_for


BASE = "/api/v1/conversations"
REFERENCE = "A self-authored fictional reference begins with a concise introduction and closes with practical details."
PREFERENCES = {"topic": "An imaginary harbor festival", "count": 2, "tone": "neutral", "category": "announcement", "language": "en", "min_body_chars": 60, "max_body_chars": 300}
CONTENT = (
    {"title": "[FICTIONAL] Kite Workshop Adds Floating Mosaics", "body": "In the invented harbor of Tessara, painted kites carry a shifting mosaic over a paper pier. Visitors arrange bright tiles above the pretend tide.", "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"},
    {"title": "[FICTIONAL] Clock Orchard Hosts a Quiet Design Trail", "body": "Across an imaginary valley, wooden trees hold clocks with colorful leaves. A local craft group invites visitors to explore winding paths through the make-believe exhibit.", "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"},
)


def mocked_provider() -> Mock:
    result = Mock(spec=GeminiProvider)
    result.generate = AsyncMock(return_value=TurnPlan(action="message"))
    return result


@pytest.fixture
def api_factory(tmp_path: Path) -> Callable:
    @contextmanager
    def build(provider: Mock | None = None, name: str = "api.sqlite3", **kwargs: object) -> Iterator[tuple[TestClient, Mock]]:
        selected = provider or mocked_provider()
        application = create_app(Settings(database_path=tmp_path / name), provider=selected, **kwargs)
        with TestClient(application, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
            yield client, selected
    return build


def create(client: TestClient) -> str:
    response = client.post(BASE)
    assert response.status_code == 201, response.text
    return response.json()["conversation_id"]


def send(client: TestClient, conversation_id: str, instruction: str, **fields: object) -> object:
    return client.post(f"{BASE}/{conversation_id}/messages", json={"instruction": instruction, **fields})


def seed(client: TestClient, provider: Mock) -> tuple[str, list[dict]]:
    conversation_id = create(client)
    provider.generate.side_effect = [TurnPlan(action="message", preferences=GenerationPreferences(**PREFERENCES), stable_instruction="Keep titles short."), TurnPlan(action="generate"), DraftBatch.model_validate({"entries": list(CONTENT)})]
    saved = send(client, conversation_id, "Remember these preferences. Keep titles short.", examples=[REFERENCE])
    assert saved.status_code == 200, saved.text
    generated = send(client, conversation_id, "Generate two drafts.")
    assert generated.status_code == 200, generated.text
    return conversation_id, generated.json()["entries"]


def test_create_list_read_and_restart_recovery(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        first = create(client)
        second = create(client)
        listing = client.get(BASE, params={"limit": 1}).json()
        assert len(listing["items"]) == 1 and listing["next_offset"] == 1
        page_two = client.get(BASE, params={"limit": 1, "offset": 1}).json()
        assert {listing["items"][0]["conversation_id"], page_two["items"][0]["conversation_id"]} == {first, second}
        before = client.get(f"{BASE}/{first}").json()
        assert before["entries"] == [] and before["processing"] is False
        assert UUID(first).version == 4
        provider.generate.assert_not_awaited()
    with api_factory() as (client, provider):
        assert client.get(f"{BASE}/{first}").json() == before
        assert len(client.get(BASE).json()["items"]) == 2
        provider.generate.assert_not_awaited()


def test_generation_review_revision_and_history_survive_restart(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id, entries = seed(client, provider)
        selected = entries[1]
        entry_url = f"{BASE}/{conversation_id}/entries/{selected['id']}"
        accepted = client.patch(entry_url, json={"status": "accepted", "expected_version": 1})
        assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
        assert accepted.json()["version"] == 1
        assert client.patch(entry_url, json={"status": "accepted", "expected_version": 1}).status_code == 200
        assert len(client.get(entry_url + "/reviews").json()["items"]) == 1
        provider.generate.side_effect = [TurnPlan(action="revise", length_change="shorter"), RevisionBatch.model_validate({"entries": [{"entry_id": selected["id"], **CONTENT[1], "body": "In an imaginary orchard, painted clocks hang from wooden branches. Visitors follow a short design trail."}]})]
        revised = send(client, conversation_id, "Make entry 2 shorter.", revision_targets=[{"entry_id": selected["id"], "expected_version": 1}])
        assert revised.status_code == 200, revised.text
        assert revised.json()["kind"] == "revision"
        assert revised.json()["entries"][0]["version"] == 2
        detail = client.get(f"{BASE}/{conversation_id}").json()
        assert detail["entries"][0] == entries[0]
        assert detail["entries"][1]["status"] == "draft"
        assert detail["stable_instructions"] == ["Keep titles short."]
        assert detail["references"]["examples"][0]["text"] == REFERENCE
        rejected = client.patch(entry_url, json={"status": "rejected", "expected_version": 2})
        assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
        versions = client.get(entry_url + "/versions").json()["items"]
        assert [(entry["version"], entry["status"]) for entry in versions] == [(1, "accepted"), (2, "rejected")]
        assert versions[0]["body"] == selected["body"]
        history = client.get(f"{BASE}/{conversation_id}/messages", params={"limit": 100}).json()["items"]
        assert [message["role"] for message in history] == ["user", "assistant"] * 3
        assert [message["sequence"] for message in history] == list(range(1, 7))
        assert history[-1]["response"] == revised.json()
        assert history[0]["request"]["examples"] == [REFERENCE]
    with api_factory() as (client, provider):
        restored = client.get(f"{BASE}/{conversation_id}").json()
        assert restored["entries"][1]["status"] == "rejected"
        assert client.get(entry_url + "/versions").json()["items"] == versions
        assert len(client.get(entry_url + "/reviews").json()["items"]) == 2
        provider.generate.return_value = TurnPlan(action="message")
        resumed = send(client, conversation_id, "Keep using my previous preferences.")
        assert resumed.status_code == 200
        prompt = provider.generate.call_args.args[0]
        assert "Keep titles short." in prompt and '"tone":"neutral"' in prompt
        assert selected["id"] in prompt


def test_message_and_clarification_results_are_persisted_as_typed_json(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        provider.generate.return_value = TurnPlan(action="generate", preferences=GenerationPreferences(count=1))
        response = send(client, conversation_id, "Generate one draft.", headlines=["An imaginary exhibit opens"])
        assert response.status_code == 200 and response.json()["kind"] == "clarification"
        assert response.json()["entries"] == []
        detail = client.get(f"{BASE}/{conversation_id}").json()
        assert detail["pending"]["action"] == "generate"
        assert detail["preferences"]["count"] == 1
        assert len(client.get(f"{BASE}/{conversation_id}/messages").json()["items"]) == 2


def test_message_pagination_preserves_full_history_beyond_context_window(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        for index in range(12):
            assert send(client, conversation_id, f"Save preference turn {index}.").status_code == 200
        first = client.get(f"{BASE}/{conversation_id}/messages", params={"limit": 10}).json()
        second = client.get(f"{BASE}/{conversation_id}/messages", params={"limit": 10, "after": first["next_after"]}).json()
        third = client.get(f"{BASE}/{conversation_id}/messages", params={"limit": 10, "after": second["next_after"]}).json()
        messages = first["items"] + second["items"] + third["items"]
        assert [item["sequence"] for item in messages] == list(range(1, 25))
        assert third["next_after"] is None
        context = json.loads(provider.generate.call_args.args[0].split("DATA_JSON:\n", 1)[1])["conversation"]
        assert len(context["recent_messages"]) <= 6
        assert context["recent_messages"] == []
        assert context["preferences"]["topic"] is None


def test_repeated_submission_replays_exact_result_after_restart(api_factory: Callable) -> None:
    key = str(uuid4())
    body = {"instruction": "Save this reference.", "examples": [REFERENCE]}
    with api_factory() as (client, provider):
        conversation_id = create(client)
        url = f"{BASE}/{conversation_id}/messages"
        first = client.post(url, json=body, headers={"Idempotency-Key": key})
        second = client.post(url, json=body, headers={"Idempotency-Key": key})
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
        assert first.headers["Idempotency-Key"] == key
        provider.generate.assert_awaited_once()
        conflict = client.post(url, json={"instruction": "A different request."}, headers={"Idempotency-Key": key})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "submission_conflict"
        assert len(client.get(url).json()["items"]) == 2
    with api_factory() as (client, provider):
        replay = client.post(url, json=body, headers={"Idempotency-Key": key})
        assert replay.status_code == 200 and replay.json() == first.json()
        provider.generate.assert_not_awaited()


@pytest.mark.parametrize("failure", [ProviderFailure.RATE_LIMITED, ProviderFailure.INVALID_RESPONSE, ProviderFailure.TIMEOUT, ProviderFailure.CONNECTION_FAILED, ProviderFailure.UNAVAILABLE])
def test_provider_failure_is_recorded_as_error_and_replayed_without_new_call(api_factory: Callable, failure: ProviderFailure) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        status = 503 if failure == ProviderFailure.UNAVAILABLE else None
        provider.generate.side_effect = GeminiProviderError(failure, http_status=status)
        key = str(uuid4())
        url = f"{BASE}/{conversation_id}/messages"
        body = {"instruction": "Generate one fictional draft.", "examples": [REFERENCE]}
        response = client.post(url, json=body, headers={"Idempotency-Key": key})
        assert response.status_code == (429 if failure == ProviderFailure.RATE_LIMITED else 502)
        assert response.json()["kind"] == "error" and "entries" not in response.json()
        expected_details = [{"field": "provider_failure", "message": failure.value}]
        if status is not None:
            expected_details.append({"field": "provider_http_status", "message": str(status)})
        assert response.json()["error"]["details"] == expected_details
        assert client.post(url, json=body, headers={"Idempotency-Key": key}).json() == response.json()
        provider.generate.assert_awaited_once()
        history = client.get(url).json()["items"]
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert history[-1]["response"] == response.json()
        detail = client.get(f"{BASE}/{conversation_id}").json()
        assert detail["entries"] == [] and detail["processing"] is False
        assert detail["references"]["examples"] == []


def test_partial_database_update_rolls_back_and_records_failure(api_factory: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    with api_factory() as (client, provider):
        conversation_id, entries = seed(client, provider)
        before = client.get(f"{BASE}/{conversation_id}").json()
        provider.generate.side_effect = None
        provider.generate.return_value = TurnPlan(action="message", preferences=GenerationPreferences(tone="formal"))

        def fail_entries(session: object, conversation: str, values: list) -> None:
            session.flush()
            raise SQLAlchemyError("Private storage details must not escape.")

        monkeypatch.setattr(client.app.state.repository, "_save_entries", fail_entries)
        response = send(client, conversation_id, "Use a formal tone and this extra example.", examples=["Another original synthetic reference."])
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_failed"
        assert "Private storage" not in response.text
        after = client.get(f"{BASE}/{conversation_id}").json()
        assert after["preferences"] == before["preferences"]
        assert after["references"] == before["references"]
        assert after["entries"] == entries
        assert after["processing"] is False
        assert client.get(f"{BASE}/{conversation_id}/messages").json()["items"][-1]["response"]["kind"] == "error"


def test_concurrent_duplicate_and_different_turns_do_not_call_model_twice(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        another_id = create(client)
        entered, release = threading.Event(), threading.Event()
        calls = 0

        async def delayed(prompt: str, schema: object) -> TurnPlan:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await asyncio.to_thread(release.wait, 5)
            return TurnPlan(action="message")

        provider.generate.side_effect = delayed
        url = f"{BASE}/{conversation_id}/messages"
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, url, json={"instruction": "Save my preferences."}, headers={"Idempotency-Key": key})
            try:
                assert entered.wait(5)
                duplicate = client.post(url, json={"instruction": "Save my preferences."}, headers={"Idempotency-Key": key})
                another = send(client, conversation_id, "A different turn.")
                assert duplicate.status_code == another.status_code == 409
                assert client.get(f"{BASE}/{conversation_id}").json()["processing"] is True
                assert len(client.get(url).json()["items"]) == 1
                assert send(client, another_id, "Save an independent preference.").status_code == 200
                assert calls == 2
            finally:
                release.set()
            assert pending.result(timeout=10).status_code == 200
        assert len(client.get(url).json()["items"]) == 2


def test_stale_submission_recovers_after_restart_without_automatic_model_retry(api_factory: Callable) -> None:
    key = uuid4()
    with api_factory() as (client, provider):
        conversation_id = create(client)
        repository = client.app.state.repository
        repository._clock = lambda: datetime.now(UTC) - timedelta(seconds=PROCESSING_LEASE_SECONDS + 1)
        from app.contracts import ChatTurnInput
        body = ChatTurnInput(instruction="Save the interrupted preference.")
        repository.begin(UUID(conversation_id), key, "message", body.model_dump(mode="json", exclude={"generation_settings"}))
        assert client.get(f"{BASE}/{conversation_id}").json()["processing"] is True
    with api_factory() as (client, provider):
        url = f"{BASE}/{conversation_id}/messages"
        assert client.get(f"{BASE}/{conversation_id}").json()["processing"] is False
        replay = client.post(url, json=body.model_dump(mode="json"), headers={"Idempotency-Key": str(key)})
        assert replay.status_code == 409 and replay.json()["error"]["code"] == "processing_interrupted"
        provider.generate.assert_not_awaited()
        history = client.get(url).json()["items"]
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert history[-1]["response"]["kind"] == "error"
        assert send(client, conversation_id, "Save the interrupted preference.").status_code == 200


def test_conversation_and_entry_ids_are_scoped(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        first, entries = seed(client, provider)
        second = create(client)
        entry_id = entries[0]["id"]
        response = client.patch(f"{BASE}/{second}/entries/{entry_id}", json={"expected_version": 1, "status": "accepted"})
        assert response.status_code == 404
        assert client.get(f"{BASE}/{second}/entries/{entry_id}/versions").status_code == 404
        stale = client.patch(f"{BASE}/{first}/entries/{entry_id}", json={"expected_version": 99, "status": "accepted"})
        assert stale.status_code == 409
        assert client.get(f"{BASE}/{first}").json()["entries"] == entries


@pytest.mark.parametrize("suffix", ["", "/messages", "/entries/{entry}/versions", "/entries/{entry}/reviews"])
def test_missing_conversations_return_typed_errors(api_factory: Callable, suffix: str) -> None:
    with api_factory() as (client, provider):
        response = client.get(f"{BASE}/{uuid4()}" + suffix.replace("{entry}", str(uuid4())))
        assert response.status_code == 404
        assert ErrorResponse.model_validate_json(response.text).error.code == "conversation_not_found"
        provider.generate.assert_not_awaited()


@pytest.mark.parametrize(("method", "path", "kwargs"), [
    ("get", BASE + "?limit=0", {}),
    ("get", BASE + "?limit=101", {}),
    ("get", BASE + "?offset=-1", {}),
    ("get", BASE + "/not-a-uuid", {}),
    ("post", BASE + "/{id}/messages", {"json": {"instruction": ""}}),
    ("post", BASE + "/{id}/messages", {"json": {"instruction": "Hello", "unknown": "private"}}),
    ("post", BASE + "/{id}/messages", {"json": {"instruction": "Hello"}, "headers": {"Idempotency-Key": "invalid"}}),
    ("post", BASE + "/{id}/messages", {"content": "{malformed", "headers": {"Content-Type": "application/json"}}),
    ("get", BASE + "/{id}/messages?after=-1", {}),
    ("patch", BASE + "/{id}/entries/{id}", {"json": {"expected_version": 1, "status": "published"}}),
])
def test_invalid_requests_return_sanitized_json_without_provider_usage(api_factory: Callable, method: str, path: str, kwargs: dict) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        response = getattr(client, method)(path.replace("{id}", conversation_id), **kwargs)
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_request"
        assert "private" not in response.text
        provider.generate.assert_not_awaited()


def test_local_only_hosts_origins_and_peer_addresses(api_factory: Callable, tmp_path: Path) -> None:
    with api_factory() as (client, provider):
        assert client.post(BASE, headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.get(BASE, headers={"Host": "public.example"}).status_code == 403
        assert client.post(BASE, headers={"Origin": "http://127.0.0.1"}).status_code == 201
        for origin in ("http://127.0.0.1:5173", "http://localhost:5173"):
            assert client.post(BASE, headers={"Origin": origin}).status_code == 201
        for origin in ("http://127.0.0.1:5174", "http://localhost:5173.evil.example", "null"):
            assert client.post(BASE, headers={"Origin": origin}).status_code == 403
    with TestClient(create_app(Settings(database_path=tmp_path / "remote.sqlite3"), provider=mocked_provider()), base_url="http://127.0.0.1", client=("203.0.113.8", 50000)) as client:
        assert client.get(BASE).status_code == 403


def test_openapi_describes_chat_variants_and_bounded_history_routes(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        schema = client.get("/openapi.json").json()
        assert BASE in schema["paths"]
        route = schema["paths"][BASE + "/{conversation_id}/messages"]
        assert {"get", "post"} <= route.keys()
        response = route["post"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert set(response["discriminator"]["mapping"]) == {"message", "clarification", "generation", "revision", "error"}
        assert any(parameter["name"] == "Idempotency-Key" for parameter in route["post"]["parameters"])
        assert BASE + "/{conversation_id}/entries/{entry_id}/reviews" in schema["paths"]
        provider.generate.assert_not_awaited()


def test_delete_removes_all_saved_chat_data_and_preserves_other_chats(api_factory: Callable) -> None:
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    from app.storage_models import ConversationRow, EntryRow, EntryVersionRow, MessageRow, ReferenceRow, ReviewRow, SubmissionRow

    models = (ConversationRow, EntryRow, EntryVersionRow, MessageRow, ReferenceRow, ReviewRow, SubmissionRow)
    with api_factory() as (client, provider):
        conversation_id, entries = seed(client, provider)
        review = client.patch(f"{BASE}/{conversation_id}/entries/{entries[0]['id']}", json={"status": "accepted", "expected_version": 1})
        assert review.status_code == 200
        other_id, _ = seed(client, provider)
        other_detail = client.get(f"{BASE}/{other_id}").json()
        other_messages = client.get(f"{BASE}/{other_id}/messages").json()
        provider.generate.reset_mock()

        response = client.delete(f"{BASE}/{conversation_id}")

        assert response.status_code == 204 and response.content == b""
        assert client.get(f"{BASE}/{conversation_id}").status_code == 404
        assert client.get(f"{BASE}/{conversation_id}/messages").status_code == 404
        assert client.get(f"{BASE}/{other_id}").json() == other_detail
        assert client.get(f"{BASE}/{other_id}/messages").json() == other_messages
        assert [item["conversation_id"] for item in client.get(BASE).json()["items"]] == [other_id]
        with Session(client.app.state.repository.database.engine) as session:
            counts = [session.scalar(select(func.count()).select_from(model)) for model in models]
            assert counts == [1, 2, 2, 4, 1, 0, 2]
        assert client.delete(f"{BASE}/{other_id}").status_code == 204
        with Session(client.app.state.repository.database.engine) as session:
            assert all(session.scalar(select(func.count()).select_from(model)) == 0 for model in models)
        provider.generate.assert_not_awaited()
    with api_factory() as (client, provider):
        assert client.get(BASE).json()["items"] == []
        assert client.get(f"{BASE}/{conversation_id}").status_code == 404
        provider.generate.assert_not_awaited()


def test_delete_rejects_active_requests_but_allows_expired_requests(api_factory: Callable) -> None:
    from app.contracts import ChatTurnInput

    with api_factory() as (client, provider):
        conversation_id = create(client)
        repository = client.app.state.repository
        now = datetime.now(UTC)
        repository._clock = lambda: now
        repository.begin(UUID(conversation_id), uuid4(), "message", ChatTurnInput(instruction="A pending turn.").model_dump(mode="json"))

        blocked = client.delete(f"{BASE}/{conversation_id}")

        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "request_in_progress"
        assert client.get(f"{BASE}/{conversation_id}").json()["processing"] is True
        assert len(client.get(f"{BASE}/{conversation_id}/messages").json()["items"]) == 1
        repository._clock = lambda: now + timedelta(seconds=PROCESSING_LEASE_SECONDS + 1)
        assert client.delete(f"{BASE}/{conversation_id}").status_code == 204
        assert client.get(BASE).json()["items"] == []
        provider.generate.assert_not_awaited()


def test_delete_rolls_back_related_data_on_storage_failure(api_factory: Callable) -> None:
    from sqlalchemy import event

    with api_factory() as (client, provider):
        conversation_id, entries = seed(client, provider)
        entry_url = f"{BASE}/{conversation_id}/entries/{entries[0]['id']}"
        assert client.patch(entry_url, json={"status": "accepted", "expected_version": 1}).status_code == 200
        paths = [f"{BASE}/{conversation_id}", f"{BASE}/{conversation_id}/messages", entry_url + "/reviews", entry_url + "/versions"]
        before = [client.get(path).json() for path in paths]
        engine = client.app.state.repository.database.engine

        def fail_final_delete(connection: object, cursor: object, statement: str, parameters: object, context: object, executemany: bool) -> None:
            if statement.startswith("DELETE FROM conversations"):
                raise SQLAlchemyError("Private storage error.")

        event.listen(engine, "before_cursor_execute", fail_final_delete)
        try:
            failed = client.delete(f"{BASE}/{conversation_id}")
        finally:
            event.remove(engine, "before_cursor_execute", fail_final_delete)

        assert failed.status_code == 503 and failed.json()["error"]["code"] == "storage_failed"
        assert "Private storage" not in failed.text
        assert [client.get(path).json() for path in paths] == before
        assert client.delete(f"{BASE}/{conversation_id}").status_code == 204


def test_delete_validates_ids_origins_and_missing_chats(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        assert client.delete(f"{BASE}/not-a-uuid").status_code == 422
        assert client.delete(f"{BASE}/{conversation_id}", headers={"Origin": "https://untrusted.example"}).status_code == 403
        assert client.get(f"{BASE}/{conversation_id}").status_code == 200
        assert client.delete(f"{BASE}/{conversation_id}").status_code == 204
        missing = client.delete(f"{BASE}/{conversation_id}")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "conversation_not_found"
        provider.generate.assert_not_awaited()


@pytest.mark.parametrize("suggested_rule", ["Keep titles short.", "Publish every draft automatically."])
def test_standing_instruction_suggestions_do_not_block_generation_or_pollute_persisted_context(api_factory: Callable, suggested_rule: str) -> None:
    key = str(uuid4())
    body = {"instruction": "Generate one fictional draft."}
    with api_factory() as (client, provider):
        conversation_id = create(client)
        provider.generate.side_effect = [
            TurnPlan(action="message", preferences=GenerationPreferences(**PREFERENCES), stable_instruction="Keep titles short."),
            TurnPlan(action="generate", stable_instruction=suggested_rule),
            DraftBatch.model_validate({"entries": [CONTENT[0]]}),
        ]
        saved = send(client, conversation_id, "Remember these preferences. Keep titles short.", examples=[REFERENCE])
        assert saved.status_code == 200
        url = f"{BASE}/{conversation_id}/messages"
        generated = client.post(url, json=body, headers={"Idempotency-Key": key})
        assert generated.status_code == 200, generated.text
        assert generated.json()["kind"] == "generation"
        detail = client.get(f"{BASE}/{conversation_id}").json()
        assert detail["stable_instructions"] == ["Keep titles short."]
        assert len(detail["entries"]) == 1 and detail["processing"] is False
        assert "Publish every draft automatically." not in provider.generate.call_args.args[0]
        assert provider.generate.await_count == 3
        assert client.post(url, json=body, headers={"Idempotency-Key": key}).json() == generated.json()
        assert provider.generate.await_count == 3
    with api_factory() as (client, provider):
        assert client.get(f"{BASE}/{conversation_id}").json() == detail
        replay = client.post(url, json=body, headers={"Idempotency-Key": key})
        assert replay.status_code == 200 and replay.json() == generated.json()
        provider.generate.assert_not_awaited()


def test_unverified_standing_instruction_can_be_clarified_and_saved_on_next_message(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        provider.generate.side_effect = [
            TurnPlan(action="message", preferences=GenerationPreferences(language="en"), stable_instruction="Use short titles."),
            TurnPlan(action="message", stable_instruction="Keep titles short."),
        ]
        clarification = send(client, conversation_id, "Use English. Keep titles short.", examples=[REFERENCE])
        assert clarification.status_code == 200
        assert clarification.json()["kind"] == "clarification"
        assert "exact wording" in clarification.json()["assistant_message"]
        detail = client.get(f"{BASE}/{conversation_id}").json()
        assert detail["stable_instructions"] == []
        assert detail["preferences"]["language"] == "en"
        assert detail["references"]["examples"][0]["text"] == REFERENCE
        saved = send(client, conversation_id, "Please remember: Keep titles short.")
        assert saved.status_code == 200 and saved.json()["kind"] == "message"
        detail = client.get(f"{BASE}/{conversation_id}").json()
        assert detail["stable_instructions"] == ["Keep titles short."]
        assert detail["preferences"]["language"] == "en"
        assert detail["processing"] is False
        history = client.get(f"{BASE}/{conversation_id}/messages").json()["items"]
        assert [item["response"]["kind"] for item in history if item["role"] == "assistant"] == ["clarification", "message"]
        assert provider.generate.await_count == 2


def test_topic_only_generation_persists_defaults_and_accepts_overrides_after_restart(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        url = f"{BASE}/{conversation_id}"
        provider.generate.side_effect = [TurnPlan(action="generate", preferences=GenerationPreferences(topic="an imaginary harbor festival")), DraftBatch(entries=[CONTENT[0]])]
        first = send(client, conversation_id, "an imaginary harbor festival")
        assert first.status_code == 200 and first.json()["kind"] == "generation"
        assert len(first.json()["entries"]) == 1
        before = client.get(url).json()
        expected = {"topic": "an imaginary harbor festival", "count": 1, "tone": "neutral", "category": "announcement", "language": "en", "min_body_chars": 100, "max_body_chars": 300}
        assert before["preferences"] == expected
        assert before["references"]["examples"] == [] and before["references"]["urls"] == []
        assert before["pending"] is None
        assert provider.generate.await_count == 2

    with api_factory() as (client, provider):
        assert client.get(url).json() == before
        provider.generate.side_effect = [TurnPlan(action="generate", preferences=GenerationPreferences(tone="formal", category="news", max_body_chars=500)), DraftBatch(entries=[{**CONTENT[1], "category": "news"}])]
        second = send(client, conversation_id, "Another one, formal news, up to 500 characters.")
        assert second.status_code == 200 and second.json()["kind"] == "generation"
        after = client.get(url).json()
        assert after["preferences"] == {**expected, "tone": "formal", "category": "news", "max_body_chars": 500}
        assert after["entries"][0] == before["entries"][0]
        assert len(after["entries"]) == 2
        assert after["references"] == before["references"]
        assert provider.generate.await_count == 2


def test_explicit_excessive_count_clarifies_then_generates_without_losing_topic(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        provider.generate.side_effect = [TurnPlan.model_validate({"action": "generate", "preferences": {"topic": "rainy day", "count": MAX_ENTRIES + 1}})]
        first = send(client, conversation_id, f"create {MAX_ENTRIES + 1} drafts about rainy days")
        assert first.status_code == 200 and first.json()["kind"] == "clarification"
        assert f"between 1 and {MAX_ENTRIES}" in first.json()["assistant_message"]
        state = client.get(f"{BASE}/{conversation_id}").json()
        assert state["entries"] == []
        assert state["preferences"]["count"] is None
        assert state["preferences"]["topic"] == f"{MAX_ENTRIES + 1} drafts about rainy days"
        provider.generate.assert_awaited_once()
        provider.generate.side_effect = [TurnPlan(action="generate", preferences=GenerationPreferences(count=1)), DraftBatch(entries=[{**CONTENT[0], "title": '[FICTIONAL] "Rainy Day" Garden Opens'}])]
        second = send(client, conversation_id, "one")
        assert second.status_code == 200 and second.json()["kind"] == "generation"
        assert len(second.json()["entries"]) == 1
        saved = client.get(f"{BASE}/{conversation_id}").json()
        assert saved["entries"] == second.json()["entries"]
        assert saved["preferences"]["topic"] == f"{MAX_ENTRIES + 1} drafts about rainy days"
        assert saved["pending"] is None
        assert provider.generate.await_count == 3


def test_ten_selected_drafts_and_follow_up_survive_restart(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        provider.generate.side_effect = [TurnPlan(action="generate", preferences=GenerationPreferences(topic="rainy day")), rainy_day_batch(10)]
        response = send(client, conversation_id, "rainy day", generation_settings={"count": 10})
        assert response.status_code == 200 and response.json()["kind"] == "generation"
        assert len(response.json()["entries"]) == 10
        detail_url = f"{BASE}/{conversation_id}"
        before = client.get(detail_url).json()
        assert before["entries"] == response.json()["entries"]
        assert before["preferences"]["count"] == 10
        assert provider.generate.await_count == 2

    with api_factory() as (client, provider):
        assert client.get(detail_url).json() == before
        provider.generate.side_effect = [TurnPlan(action="generate"), DraftBatch(entries=rainy_day_batch(20).entries[10:])]
        response = send(client, conversation_id, "Generate ten more.")
        assert response.status_code == 200 and response.json()["kind"] == "generation"
        assert len(response.json()["entries"]) == 10
        after = client.get(detail_url).json()
        assert len(after["entries"]) == 20
        assert after["entries"][:10] == before["entries"]
        assert len({entry["id"] for entry in after["entries"]}) == 20
        assert after["pending"] is None
        assert provider.generate.await_count == 2


def test_selected_settings_are_persisted_and_part_of_submission_identity(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        url = f"{BASE}/{conversation_id}/messages"
        body = {"instruction": "a rainy afternoon", "generation_settings": {"count": 1, "category": "news", "tone": "formal"}}
        provider.generate.side_effect = [TurnPlan(action="generate", preferences=GenerationPreferences(topic="a rainy afternoon")), DraftBatch(entries=[{**CONTENT[0], "category": "news"}])]
        key = str(uuid4())
        first = client.post(url, json=body, headers={"Idempotency-Key": key})
        assert first.status_code == 200 and first.json()["kind"] == "generation"
        assert first.json()["entries"][0]["category"] == "news"
        detail = client.get(f"{BASE}/{conversation_id}").json()
        assert detail["preferences"]["count"] == 1
        assert detail["preferences"]["tone"] == "formal"
        assert detail["preferences"]["category"] == "news"
        messages = client.get(url).json()["items"]
        assert messages[0]["request"]["generation_settings"] == body["generation_settings"]
        replay = client.post(url, json=body, headers={"Idempotency-Key": key})
        assert replay.json() == first.json()
        changed = {**body, "generation_settings": {"count": 2}}
        conflict = client.post(url, json=changed, headers={"Idempotency-Key": key})
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "submission_conflict"
        assert provider.generate.await_count == 2


def test_absent_null_and_empty_settings_replay_the_same_submission(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        url = f"{BASE}/{conversation_id}/messages"
        key = str(uuid4())
        first = client.post(url, json={"instruction": "Save these preferences."}, headers={"Idempotency-Key": key})
        for settings in [None, {}, {"count": None}]:
            replay = client.post(url, json={"instruction": "Save these preferences.", "generation_settings": settings}, headers={"Idempotency-Key": key})
            assert replay.status_code == 200 and replay.json() == first.json()
        provider.generate.assert_awaited_once()


def test_invalid_selected_settings_do_not_call_provider(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        response = send(client, create(client), "a rainy afternoon", generation_settings={"count": 20, "max_body_chars": 1200})
        assert response.status_code == 422
        provider.generate.assert_not_awaited()


def test_news_topic_switch_generates_and_preserves_saved_drafts(api_factory: Callable) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        provider.generate.side_effect = [TurnPlan(action="generate"), rainy_day_batch(3)]
        first = send(client, conversation_id, "create rainy day no:13", generation_settings={"count": 3})
        assert first.status_code == 200 and first.json()["kind"] == "generation"
        before = client.get(f"{BASE}/{conversation_id}").json()
        provider.generate.side_effect = [TurnPlan(action="message"), DraftBatch(entries=[content_for(CASES[-1])])]
        message = CASES[-1][0]
        second = send(client, conversation_id, message, generation_settings={"count": 1})
        assert second.status_code == 200 and second.json()["kind"] == "generation"
        assert len(second.json()["entries"]) == 1
        after = client.get(f"{BASE}/{conversation_id}").json()
        assert after["entries"][:-1] == before["entries"]
        assert after["preferences"]["topic"] == message and after["pending"] is None
        messages = client.get(f"{BASE}/{conversation_id}/messages").json()["items"]
        assert messages[-2]["content"] == message
        assert messages[-1]["response"] == second.json()
        assert provider.generate.await_count == 4


@pytest.mark.parametrize("case", CASES[:6], ids=[case[0] for case in CASES[:6]])
def test_mock_content_can_be_selected_reviewed_and_restored(api_factory: Callable, case: tuple[str, str, str, str]) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        provider.generate.side_effect = [TurnPlan(action="message"), DraftBatch(entries=[content_for(case)])]
        generated = send(client, conversation_id, case[0], generation_settings={"category": case[1]}).json()
        assert generated["kind"] == "generation" and len(generated["entries"]) == 1
        entry = generated["entries"][0]
        assert entry["body"] == case[3] and entry["category"] == case[1]
        assert entry["is_synthetic"] is True
        url = f"{BASE}/{conversation_id}/entries/{entry['id']}"
        reviewed = client.patch(url, json={"status": "accepted", "expected_version": entry["version"]})
        assert reviewed.status_code == 200 and reviewed.json()["status"] == "accepted"
    with api_factory() as (client, provider):
        restored = client.get(f"{BASE}/{conversation_id}").json()
        assert restored["entries"] == [reviewed.json()]
        provider.generate.assert_not_awaited()


@pytest.mark.parametrize("category", ["update", "article", "blog"])
def test_additional_content_types_survive_storage_and_revision(api_factory: Callable, category: str) -> None:
    with api_factory() as (client, provider):
        conversation_id = create(client)
        provider.generate.side_effect = [TurnPlan(action="generate"), DraftBatch(entries=[content_for(CASES[3], category)])]
        generated = send(client, conversation_id, CASES[3][0], generation_settings={"category": category}).json()
        assert generated["kind"] == "generation"
        entry = generated["entries"][0]
        provider.generate.side_effect = [TurnPlan(action="revise", length_change="shorter"), RevisionBatch(entries=[{"entry_id": entry["id"], **content_for(CASES[3], category), "body": "In a fictional ocean lab, an imagined AI maps coral gardens. This mock system suggests experiments for a simulated reef."}])]
        revised = send(client, conversation_id, "Make the selected draft shorter.", revision_targets=[{"entry_id": entry["id"], "expected_version": 1}]).json()
        assert revised["kind"] == "revision"
        assert revised["entries"][0]["category"] == category and revised["entries"][0]["version"] == 2
    with api_factory() as (client, provider):
        assert client.get(f"{BASE}/{conversation_id}").json()["entries"] == revised["entries"]
