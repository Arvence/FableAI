from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from credential_helpers import administrator_headers

from app.config import Settings
from app.contracts import DraftBatch
from app.generation.gemini_provider import GeminiProvider
from app.main import create_app
from app.persistence.database import Database


BASE = "/api/v1/projects"


def sample(text: str = "An original fictional sample about a paper harbor.", reference_id: str | None = None) -> dict:
    return {"id": reference_id or str(uuid4()), "text": text, "authorization": {"basis": "owned", "evidence": "Written by our editorial team.", "model_use_allowed": True, "non_confidential": True}}


@pytest.fixture(params=["sqlite", "postgres"])
def project_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Database]:
    if request.param == "postgres":
        yield request.getfixturevalue("migrated_postgres")
    else:
        database = Database(tmp_path / "projects.sqlite3")
        database.initialize()
        try:
            yield database
        finally:
            database.close()


@pytest.fixture
def project_api_factory(project_database: Database):
    headers = administrator_headers(project_database)
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(side_effect=AssertionError("Project configuration must never invoke generation."))
    importer = Mock(side_effect=AssertionError("Saving samples must never import external content."))

    @contextmanager
    def open_client():
        settings = Settings(database_path=project_database.path) if project_database.path is not None else Settings(database_url=project_database.url.render_as_string(hide_password=False))
        with TestClient(create_app(settings, provider=provider, importer_factory=importer), base_url="http://127.0.0.1", client=("127.0.0.1", 50000), headers=headers) as client:
            yield client

    yield open_client
    provider.generate.assert_not_called()
    importer.assert_not_called()


def create(client: TestClient, project_id: str = "wowiki", **fields: object) -> dict:
    response = client.post(BASE, json={"project_id": project_id, "display_name": project_id, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def test_two_projects_keep_independent_settings_samples_and_ids_across_restarts(project_api_factory) -> None:
    shared_id = str(uuid4())
    first_sample = sample(reference_id=shared_id)
    other_sample = sample("A different authorized sample for a fictional club.", shared_id)
    with project_api_factory() as client:
        first = create(client, display_name="WoWiki", generation_settings={"category": "news", "tone": "informative", "language": "en", "count": 2, "min_body_chars": 300, "max_body_chars": 700}, instructions=["Keep each draft fictional."], references=[first_sample])
        second = create(client, "club", display_name="Fictional Club", generation_settings={"category": "blog", "tone": "playful", "language": "tr", "count": 1, "min_body_chars": 700, "max_body_chars": 1200, "body_length_mode": "strict"}, instructions=["Use short paragraphs."], references=[other_sample])
    with project_api_factory() as client:
        assert client.get(BASE + "/wowiki").json() == first
        assert client.get(BASE + "/club").json() == second
        updated = client.patch(BASE + "/wowiki", json={"generation_settings": {"count": 3}, "display_name": "WoWiki Editorial"}).json()
        assert updated["project_id"] == "wowiki"
        assert updated["created_at"] == first["created_at"]
        assert updated["updated_at"] > first["updated_at"]
        assert updated["generation_settings"] == {**first["generation_settings"], "count": 3}
        assert updated["references"] == first["references"]
        assert updated["instructions"] == first["instructions"]
        assert client.get(BASE + "/club").json() == second
    with project_api_factory() as client:
        assert client.get(BASE + "/wowiki").json() == updated
        assert client.get(BASE + "/club").json() == second


def test_complete_defaults_and_explicit_list_replacement(project_api_factory) -> None:
    with project_api_factory() as client:
        original = create(client, references=[sample()], instructions=["Keep titles concise."])
        peer = create(client, "peer", references=[sample("A separate project sample.", original["references"][0]["id"])])
        assert original["generation_settings"] == {"body_length_mode": "target", "category": "announcement", "tone": "neutral", "language": "en", "count": 1, "min_body_chars": 100, "max_body_chars": 300}
        replacement = sample("An original fictional replacement sample.")
        response = client.patch(BASE + "/wowiki", json={"references": [replacement]})
        assert response.status_code == 200
        assert response.json()["references"] == [replacement]
        assert response.json()["instructions"] == original["instructions"]
        cleared = client.patch(BASE + "/wowiki", json={"references": [], "instructions": []})
        assert cleared.status_code == 200
        assert cleared.json()["references"] == cleared.json()["instructions"] == []
        assert client.get(BASE + "/peer").json() == peer
    with project_api_factory() as client:
        assert client.get(BASE + "/wowiki").json() == cleared.json()
        assert client.get(BASE + "/peer").json() == peer


def test_server_assigned_sample_ids_are_stable(project_api_factory) -> None:
    reference = sample()
    del reference["id"]
    with project_api_factory() as client:
        saved = create(client, references=[reference])
        assert saved["references"][0]["id"]
    with project_api_factory() as client:
        assert client.get(BASE + "/wowiki").json()["references"] == saved["references"]


def test_duplicate_create_never_overwrites_existing_project(project_api_factory) -> None:
    with project_api_factory() as client:
        original = create(client, references=[sample()])
        duplicate = client.post(BASE, json={"project_id": "wowiki", "display_name": "Replacement", "generation_settings": {"count": 20}})
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "project_conflict"
        assert client.get(BASE + "/wowiki").json() == original


@pytest.mark.parametrize("method,path,body", [("get", "/absent", None), ("patch", "/absent", {"display_name": "Missing"}), ("get", "/absent/output-contract", None)])
def test_unknown_project_returns_stable_not_found_error(project_api_factory, method: str, path: str, body: dict | None) -> None:
    with project_api_factory() as client:
        response = client.request(method, BASE + path, json=body)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "project_not_found"
        assert client.get(BASE).json()["items"] == []


@pytest.mark.parametrize("fields", [
    {"project_id": ""}, {"project_id": "WoWiki"}, {"project_id": " wowiki "}, {"project_id": "a/b"}, {"project_id": "a" * 101}, {"project_id": 123},
    {"display_name": "  "}, {"display_name": "a" * 201},
    {"generation_settings": {"count": 0}}, {"generation_settings": {"count": 21}}, {"generation_settings": {"count": True}}, {"generation_settings": {"count": "2"}},
    {"generation_settings": {"count": 20, "max_body_chars": 1000}}, {"generation_settings": {"min_body_chars": 500, "max_body_chars": 300}},
    {"generation_settings": {"category": "unsupported"}}, {"generation_settings": {"tone": "unknown"}}, {"generation_settings": {"language": "English"}},
    {"generation_settings": {"min_body_chars": 0}}, {"generation_settings": {"max_body_chars": 6001}}, {"generation_settings": {"body_length_mode": "words"}},
    {"generation_settings": {"count": None}}, {"instructions": [""]}, {"instructions": ["a"] * 9}, {"instructions": ["a" * 2500, "b" * 2500]},
    {"references": [{"text": "No authorization supplied."}]},
    {"output_schema": {"anything": True}}, {"output_contract": "arbitrary_json"},
])
def test_invalid_project_creation_is_rejected_without_side_effects(project_api_factory, fields: dict) -> None:
    with project_api_factory() as client:
        response = client.post(BASE, json={"project_id": "wowiki", "display_name": "WoWiki", **fields})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert client.get(BASE).json()["items"] == []


@pytest.mark.parametrize("authorization", [
    {"model_use_allowed": False}, {"model_use_allowed": 1}, {"non_confidential": False}, {"evidence": " "}, {"basis": "publicly_accessible"},
])
def test_samples_require_explicit_valid_usage_authorization(project_api_factory, authorization: dict) -> None:
    reference = sample()
    reference["authorization"].update(authorization)
    with project_api_factory() as client:
        response = client.post(BASE, json={"project_id": "wowiki", "display_name": "WoWiki", "references": [reference]})
        assert response.status_code == 422
        assert client.get(BASE).json()["items"] == []


@pytest.mark.parametrize("kind", ["count", "characters", "duplicate_ids", "sample_length"])
def test_reference_limits_are_enforced(project_api_factory, kind: str) -> None:
    references = {"count": [sample() for _ in range(11)], "characters": [sample("a" * 8000) for _ in range(4)], "sample_length": [sample("a" * 10001)]}.get(kind)
    if kind == "duplicate_ids":
        reference = sample()
        references = [reference, reference]
    with project_api_factory() as client:
        response = client.post(BASE, json={"project_id": "wowiki", "display_name": "WoWiki", "references": references})
        assert response.status_code == 422
        assert client.get(BASE).json()["items"] == []


@pytest.mark.parametrize("changes", [
    {}, {"project_id": "renamed"}, {"display_name": None}, {"references": None}, {"instructions": None}, {"generation_settings": None},
    {"generation_settings": {}}, {"generation_settings": {"count": None}},
    {"generation_settings": {"min_body_chars": 701}}, {"generation_settings": {"count": 20}},
    {"instructions": ["a" * 3000, "b" * 3000]}, {"output_contract": "other"},
])
def test_invalid_update_preserves_entire_project(project_api_factory, changes: dict) -> None:
    with project_api_factory() as client:
        original = create(client, generation_settings={"count": 2, "max_body_chars": 700}, references=[sample()])
        response = client.patch(BASE + "/wowiki", json=changes)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert client.get(BASE + "/wowiki").json() == original


def test_list_pagination_is_bounded_and_stable_with_equal_timestamps(project_api_factory) -> None:
    with project_api_factory() as client:
        client.app.state.projects._clock = lambda: datetime(2026, 1, 1, tzinfo=UTC)
        for project_id in ("charlie", "alpha", "bravo"):
            create(client, project_id)
        first = client.get(BASE + "?limit=2").json()
        assert [item["project_id"] for item in first["items"]] == ["alpha", "bravo"]
        assert first["next_offset"] == 2
        assert "references" not in first["items"][0]
        second = client.get(BASE + "?limit=2&offset=2").json()
        assert [item["project_id"] for item in second["items"]] == ["charlie"]
        assert second["next_offset"] is None
        assert client.get(BASE + "?offset=99").json() == {"items": [], "next_offset": None}
        for query in ("limit=0", "limit=101", "offset=-1", "offset=1000001"):
            assert client.get(BASE + "?" + query).status_code == 422


def test_output_contract_reuses_existing_draft_batch_without_generation(project_api_factory) -> None:
    with project_api_factory() as client:
        project = create(client)
        assert project["output_contract"] == "draft_batch"
        response = client.get(BASE + "/wowiki/output-contract")
        assert response.status_code == 200
        assert response.json() == DraftBatch.model_json_schema()
        assert response.json()["properties"]["entries"]["minItems"] == 1
        openapi = client.get("/openapi.json").json()
        assert set(openapi["paths"][BASE]) == {"get", "post"}
        assert set(openapi["paths"][BASE + "/{project_id}"]) == {"get", "patch"}
        assert BASE + "/{project_id}/generations" not in openapi["paths"]


def test_failed_write_rolls_back_settings_and_replaced_references(project_api_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    with project_api_factory() as client:
        original = create(client, references=[sample()], instructions=["Keep the original rule."])
        repository = client.app.state.projects
        save = repository._save_references

        def fail_after_save(session, project_id, configuration):
            save(session, project_id, configuration)
            raise SQLAlchemyError("private connection details must not escape")

        monkeypatch.setattr(repository, "_save_references", fail_after_save)
        failed = client.patch(BASE + "/wowiki", json={"display_name": "Unsaved", "generation_settings": {"count": 3}, "instructions": ["Unsaved rule."], "references": [sample("Unsaved sample.")]})
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "storage_failed"
        assert "private connection" not in failed.text
        assert client.get(BASE + "/wowiki").json() == original


def test_concurrent_creates_claim_an_id_once(project_api_factory) -> None:
    with project_api_factory() as client:
        barrier = Barrier(2)

        def submit(name: str):
            barrier.wait(timeout=5)
            return client.post(BASE, json={"project_id": "shared", "display_name": name})

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit, ["First", "Second"]))
        assert sorted(response.status_code for response in responses) == [201, 409]
        winner = next(response.json() for response in responses if response.status_code == 201)
        assert client.get(BASE + "/shared").json() == winner


def test_concurrent_partial_updates_merge_against_current_saved_settings(project_api_factory) -> None:
    with project_api_factory() as client:
        create(client)
        other = create(client, "other")
        barrier = Barrier(2)

        def submit(changes: dict):
            barrier.wait(timeout=5)
            return client.patch(BASE + "/wowiki", json={"generation_settings": changes})

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(submit, [{"count": 3}, {"tone": "formal"}]))
        assert all(response.status_code == 200 for response in responses)
        saved = client.get(BASE + "/wowiki").json()
        assert saved["generation_settings"]["count"] == 3
        assert saved["generation_settings"]["tone"] == "formal"
        assert client.get(BASE + "/other").json() == other


def test_project_routes_keep_loopback_safeguards(project_api_factory) -> None:
    with project_api_factory() as client:
        denied = client.post(BASE, json={"project_id": "wowiki", "display_name": "WoWiki"}, headers={"Origin": "https://untrusted.example"})
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "access_denied"
        assert client.get(BASE).json()["items"] == []
