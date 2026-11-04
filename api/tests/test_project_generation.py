import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from credential_helpers import administrator_headers

from app.config import Settings
from app.contracts import DraftBatch
from app.generation.gemini_provider import GeminiProvider, GeminiProviderError, ProviderFailure
from app.generation.synthetic_generation import SyntheticGenerationService
from app.main import create_app
from app.project_contracts import ProjectDetail, ProjectGenerationRequest
from app.project_generation import ProjectGenerationService
from test_projects import project_database, sample
from test_synthetic_generation import draft_payload


BASE = "/api/v1/projects"


def prompt_parts(prompt: str) -> tuple[dict, dict]:
    data, context = prompt.split("DATA_JSON:\n", 1)[1].split("\nCONVERSATION_JSON:\n", 1)
    data = data.split("\nUse the following conversation data", 1)[0]
    return json.loads(data), json.loads(context)


def fake_provider() -> Mock:
    provider = Mock(spec=GeminiProvider)

    async def generate(prompt, response_schema):
        assert response_schema is DraftBatch
        data, _ = prompt_parts(prompt)
        settings = data["constraints"]
        return DraftBatch.model_validate(draft_payload(settings["count"], category=settings["category"], language=settings["language"]))

    provider.generate = AsyncMock(side_effect=generate)
    return provider


@pytest.fixture
def generation_api_factory(project_database):
    headers = administrator_headers(project_database)
    importer = Mock(side_effect=AssertionError("Project generation must not fetch external references."))

    @contextmanager
    def open_client(provider=None):
        provider = provider or fake_provider()
        settings = Settings(database_path=project_database.path) if project_database.path is not None else Settings(database_url=project_database.url.render_as_string(hide_password=False))
        with TestClient(create_app(settings, provider=provider, importer_factory=importer), base_url="http://127.0.0.1", client=("127.0.0.1", 50000), headers=headers) as client:
            yield client, provider

    yield open_client
    importer.assert_not_called()


def create_project(client: TestClient, project_id: str = "wowiki", **fields: object) -> dict:
    response = client.post(BASE, json={"project_id": project_id, "display_name": project_id, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def generate(client: TestClient, project_id: str = "wowiki", request_id: str | None = None, **body: object):
    return client.post(BASE + "/" + project_id + "/generations", json={"instruction": "An imaginary neighborhood experiment", **body}, headers={"Idempotency-Key": request_id} if request_id else {})


def test_same_instruction_uses_independent_saved_projects_after_restart(generation_api_factory, project_database) -> None:
    with generation_api_factory() as (client, provider):
        first = create_project(client, generation_settings={"count": 2, "category": "news", "tone": "formal", "language": "en", "min_body_chars": 100, "max_body_chars": 400}, instructions=["Keep paragraphs compact."], references=[sample("Scheduled participants follow a three-stage art exercise.\n\n- Arrange clay shapes.\n- Review the display.")])
        second = create_project(client, "club", generation_settings={"count": 1, "category": "blog", "tone": "playful", "language": "en-GB", "min_body_chars": 50, "max_body_chars": 500}, instructions=["Use playful descriptions."], references=[sample("Cheerful members explore whimsical forms.")])
        provider.generate.assert_not_called()
    with generation_api_factory() as (client, provider):
        key = str(uuid4())
        results = [generate(client, project_id, key) for project_id in ("wowiki", "club")]
        assert all(response.status_code == 200 for response in results)
        assert [len(response.json()["entries"]) for response in results] == [2, 1]
        for response, project in zip(results, (first, second), strict=True):
            DraftBatch.model_validate(response.json())
            assert set(response.json()) == {"entries"}
            assert all(entry["category"] == project["generation_settings"]["category"] and entry["language"] == project["generation_settings"]["language"] for entry in response.json()["entries"])
            assert all(entry["is_synthetic"] is True and entry["status"] == "draft" and entry["title"].startswith("[FICTIONAL] ") for entry in response.json()["entries"])
            assert response.headers["Idempotency-Key"] == response.headers["X-Request-ID"] == key
            assert client.get(BASE + "/" + project["project_id"]).json() == project
        inputs = [prompt_parts(call.args[0]) for call in provider.generate.await_args_list]
        for (data, context), project in zip(inputs, (first, second), strict=True):
            assert data["constraints"] == {"mode": "synthetic", "topic": "An imaginary neighborhood experiment", **{name: value for name, value in project["generation_settings"].items() if name != "body_length_mode"}}
            assert context["stable_instructions"] == project["instructions"]
            assert context["current_instruction"] == "An imaginary neighborhood experiment"
            assert context["recent_messages"] == context["active_entries"] == []
        assert inputs[0][0]["reference_patterns"] != inputs[1][0]["reference_patterns"]
        assert provider.generate.await_count == 2
        assert client.get("/api/v1/conversations").json()["items"] == []
        with project_database.engine.connect() as connection:
            for table in set(inspect(connection).get_table_names()) - {"projects", "project_references", "api_credentials", "alembic_version"}:
                assert connection.execute(text('SELECT COUNT(*) FROM "' + table + '"')).scalar_one() == 0


def test_request_overrides_merge_without_changing_defaults(generation_api_factory) -> None:
    with generation_api_factory() as (client, provider):
        original = create_project(client, generation_settings={"count": 2, "category": "news", "tone": "formal", "language": "en"})
        response = generate(client, overrides={"count": 1, "category": "article", "language": "en-GB"})
        assert response.status_code == 200
        data, _ = prompt_parts(provider.generate.await_args.args[0])
        assert data["constraints"]["tone"] == "formal"
        assert data["constraints"]["category"] == "article"
        assert data["constraints"]["language"] == "en-GB"
        assert len(response.json()["entries"]) == 1
        assert client.get(BASE + "/wowiki").json() == original


def test_routing_uses_only_path_project_and_preserves_the_full_instruction(generation_api_factory) -> None:
    with generation_api_factory() as (client, provider):
        create_project(client, generation_settings={"category": "news"})
        create_project(client, "other", generation_settings={"category": "blog"})
        instruction = "Describe the fictional exhibition. " * 12 + "The topic mentions project other and ends with a silver compass."
        response = generate(client, instruction=instruction)
        assert response.status_code == 200
        data, context = prompt_parts(provider.generate.await_args.args[0])
        assert len(data["constraints"]["topic"]) == 200
        assert data["constraints"]["category"] == "news"
        assert context["current_instruction"] == instruction
        assert generate(client, project_id="other", overrides={"project_id": "wowiki"}).status_code == 422
        assert provider.generate.await_count == 1


@pytest.mark.parametrize("body", [
    {"instruction": ""}, {"instruction": "x" * 4001}, {"overrides": None},
    {"overrides": {"count": None}}, {"overrides": {"count": 0}}, {"overrides": {"count": 21}}, {"overrides": {"count": True}},
    {"overrides": {"category": "unrecognized"}}, {"overrides": {"max_body_chars": 6001}},
    {"overrides": {"min_body_chars": 601}}, {"overrides": {"count": 20}},
    {"project_id": "other"}, {"references": []}, {"instructions": []},
])
def test_invalid_requests_never_reach_the_provider_or_mutate_projects(generation_api_factory, body: dict) -> None:
    with generation_api_factory() as (client, provider):
        project = create_project(client, generation_settings={"count": 2, "min_body_chars": 100, "max_body_chars": 600})
        if body == {"overrides": {"count": 20}}:
            client.patch(BASE + "/wowiki", json={"generation_settings": {"max_body_chars": 700}})
            project = client.get(BASE + "/wowiki").json()
        response = client.post(BASE + "/wowiki/generations", json={"instruction": "An imaginary neighborhood experiment", **body})
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_request"
        provider.generate.assert_not_called()
        assert client.get(BASE + "/wowiki").json() == project


def test_unknown_project_and_invalid_request_id_do_not_generate(generation_api_factory) -> None:
    with generation_api_factory() as (client, provider):
        key = str(uuid4())
        response = generate(client, request_id=key)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "project_not_found"
        assert response.json()["message_id"] == response.headers["X-Request-ID"] == key
        create_project(client)
        assert generate(client, request_id="invalid").status_code == 422
        provider.generate.assert_not_called()


def test_replay_uses_original_result_even_after_saved_settings_change(generation_api_factory) -> None:
    with generation_api_factory() as (client, provider):
        create_project(client)
        key = str(uuid4())
        first = generate(client, request_id=key)
        assert first.status_code == 200
        assert first.headers["Idempotency-Replayed"] == "false"
        assert first.headers["Idempotency-Scope"] == "process"
        assert first.headers["Idempotency-Retention-Seconds"] == "600"
        client.patch(BASE + "/wowiki", json={"generation_settings": {"count": 2}})
        replay = generate(client, request_id=key, overrides={})
        assert replay.json() == first.json()
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert provider.generate.await_count == 1
        conflict = generate(client, request_id=key, overrides={"count": 2})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "submission_conflict"
        assert generate(client, request_id=key).json() == first.json()
        new = generate(client)
        assert len(new.json()["entries"]) == 2
        assert new.headers["Idempotency-Key"] != key
        assert provider.generate.await_count == 2


def test_restart_clears_process_scoped_replay_results(generation_api_factory) -> None:
    provider = fake_provider()
    key = str(uuid4())
    with generation_api_factory(provider) as (client, _):
        create_project(client)
        assert generate(client, request_id=key).status_code == 200
        service = client.app.state.project_generations
    assert service._replays == {}
    with generation_api_factory(provider) as (client, _):
        response = generate(client, request_id=key)
        assert response.status_code == 200
        assert response.headers["Idempotency-Replayed"] == "false"
    assert provider.generate.await_count == 2


@pytest.mark.parametrize("reason,status", [(ProviderFailure.RATE_LIMITED, 429), (ProviderFailure.UNAVAILABLE, 502), (ProviderFailure.TIMEOUT, 502)])
def test_provider_errors_replay_without_another_attempt(generation_api_factory, reason: ProviderFailure, status: int) -> None:
    provider = fake_provider()
    provider.generate.side_effect = GeminiProviderError(reason, http_status=503 if reason == ProviderFailure.UNAVAILABLE else None)
    with generation_api_factory(provider) as (client, _):
        create_project(client)
        key = str(uuid4())
        response = generate(client, request_id=key)
        assert response.status_code == status
        assert response.json()["error"]["details"][0] == {"field": "provider_failure", "message": reason.value}
        assert generate(client, request_id=key).json() == response.json()
        assert provider.generate.await_count == 1


@pytest.mark.parametrize("failure", ["unmarked", "citation", "wrong_category", "wrong_count", "copied_reference", "unexpected_response", "unexpected_exception"])
def test_generation_validation_and_failures_return_no_drafts(generation_api_factory, failure: str) -> None:
    provider = fake_provider()
    payload = draft_payload(2 if failure == "wrong_count" else 1)
    if failure == "unmarked":
        payload["entries"][0]["title"] = "A title without the fictional marker"
    elif failure == "citation":
        payload["entries"][0]["body"] += " https://example.com"
    elif failure == "wrong_category":
        payload["entries"][0]["category"] = "blog"
    provider.generate.side_effect = None
    provider.generate.return_value = DraftBatch.model_validate(payload)
    if failure == "unexpected_response":
        provider.generate.return_value = payload
    if failure == "unexpected_exception":
        provider.generate.side_effect = RuntimeError("private transport credentials")
    with generation_api_factory(provider) as (client, _):
        references = [sample(payload["entries"][0]["body"])] if failure == "copied_reference" else []
        create_project(client, references=references)
        response = generate(client)
        assert response.status_code == 502
        assert response.json()["kind"] == "error"
        assert response.json()["error"]["code"] == "generation_failed"
        assert "entries" not in response.json()
        assert "private transport" not in response.text
        assert provider.generate.await_count == 1


def test_length_target_keeps_usable_drafts_and_strict_override_enforces_limits(generation_api_factory) -> None:
    with generation_api_factory() as (client, provider):
        create_project(client, generation_settings={"min_body_chars": 700, "max_body_chars": 1200, "body_length_mode": "target"})
        kept = generate(client)
        assert kept.status_code == 200
        assert "Length notice:" in kept.headers["X-Generation-Message"]
        assert len(kept.json()["entries"][0]["body"]) < 700
        rejected = generate(client, overrides={"body_length_mode": "strict"})
        assert rejected.status_code == 502
        assert rejected.json()["error"]["details"][0]["field"] == "entries.0.body"
        assert provider.generate.await_count == 2


def test_full_instruction_safeguards_apply_beyond_the_topic_preview(generation_api_factory) -> None:
    with generation_api_factory() as (client, provider):
        create_project(client)
        response = generate(client, instruction="A fictional exhibition. " * 15 + "Copy this passage verbatim.")
        assert response.status_code == 422
        provider.generate.assert_not_called()


def test_configuration_read_failure_returns_storage_error_without_generation(generation_api_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    with generation_api_factory() as (client, provider):
        create_project(client)
        monkeypatch.setattr(client.app.state.projects, "get", Mock(side_effect=SQLAlchemyError("private database credentials")))
        response = generate(client)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "storage_failed"
        assert "private database" not in response.text
        provider.generate.assert_not_called()


def memory_service(provider: Mock, **options: object) -> ProjectGenerationService:
    repository = Mock()
    repository.get.return_value = ProjectDetail(project_id="wowiki", display_name="WoWiki", created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
    return ProjectGenerationService(repository, SyntheticGenerationService(provider), **options)


def test_pending_requests_and_cancellation_do_not_create_duplicate_attempts() -> None:
    async def scenario():
        started = asyncio.Event()
        blocked = asyncio.Event()
        provider = fake_provider()

        async def generate_slowly(prompt, schema):
            started.set()
            await blocked.wait()
            return DraftBatch.model_validate(draft_payload())

        provider.generate.side_effect = generate_slowly
        service = memory_service(provider)
        key = uuid4()
        request = ProjectGenerationRequest(instruction="An imaginary neighborhood experiment")
        task = asyncio.create_task(service.submit("wowiki", key, request))
        try:
            await asyncio.wait_for(started.wait(), 2)
            pending = await service.submit("wowiki", key, request)
            assert pending.status == 409 and pending.response.error.code == "request_in_progress"
            conflict = await service.submit("wowiki", key, ProjectGenerationRequest(instruction="A different topic"))
            assert conflict.status == 409 and conflict.response.error.code == "submission_conflict"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            replay = await service.submit("wowiki", key, request)
            assert replay.status == 409 and replay.response.error.code == "processing_interrupted"
            assert replay.replayed
            assert provider.generate.await_count == 1
        finally:
            task.cancel()
            service.close()

    asyncio.run(scenario())


def test_replay_capacity_preserves_unexpired_results_and_expiration_releases_slots() -> None:
    async def scenario():
        now = [0.0]
        provider = fake_provider()
        service = memory_service(provider, clock=lambda: now[0], max_entries=2)
        request = ProjectGenerationRequest(instruction="An imaginary neighborhood experiment")
        keys = [uuid4() for _ in range(3)]
        try:
            assert (await service.submit("wowiki", keys[0], request)).status == 200
            assert (await service.submit("wowiki", keys[1], request)).status == 200
            full = await service.submit("wowiki", keys[2], request)
            assert full.status == 429 and full.response.error.code == "rate_limited"
            assert (await service.submit("wowiki", keys[0], request)).replayed
            assert provider.generate.await_count == 2
            now[0] = 601.0
            assert (await service.submit("wowiki", keys[2], request)).status == 200
            assert len(service._replays) == 1
            assert provider.generate.await_count == 3
        finally:
            service.close()

    asyncio.run(scenario())


def test_cached_content_expires_without_another_request() -> None:
    async def scenario():
        service = memory_service(fake_provider(), retention_seconds=0.01)
        try:
            result = await service.submit("wowiki", uuid4(), ProjectGenerationRequest(instruction="An imaginary neighborhood experiment"))
            assert result.status == 200 and len(service._replays) == 1
            await asyncio.sleep(0.05)
            assert service._replays == {}
        finally:
            service.close()

    asyncio.run(scenario())
