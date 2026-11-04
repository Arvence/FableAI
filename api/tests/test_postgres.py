import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.contracts import DraftBatch, ErrorInfo, ErrorResponse, MessageResponse, ReferenceCollection, ReferenceText
from app.conversation_contracts import TurnPlan
from app.generation.gemini_provider import GeminiProvider
from app.generation.revision_generation import RevisionBatch
from app.main import create_app
from app.persistence.conversation_repository import PROCESSING_LEASE_SECONDS, ConversationRepository, RepositoryError, SavedResponse, SubmissionClaim
from app.persistence.database import Database, migration_config
from app.persistence.storage_models import Base, ConversationRow, ProjectReferenceRow, ProjectRow


CONTENT = {"title": "[FICTIONAL] Paper Harbor Opens a Reading Room", "body": "In an imaginary harbor, neighbors gather in a new reading room built from painted paper. The fictional project includes shelves for shared stories and a quiet workshop.", "category": "news", "language": "en", "tags": ["fiction"], "is_synthetic": True, "status": "draft"}
BASE = "/api/v1/conversations"


def open_client(database: Database, provider: Mock) -> TestClient:
    return TestClient(create_app(Settings(local_demo=True, database_url=database.url.render_as_string(hide_password=False)), provider=provider), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def test_fresh_postgres_requires_migrations_and_root_command_initializes_schema(postgres_database: Database) -> None:
    with pytest.raises(RuntimeError, match="migrations are required"):
        postgres_database.initialize()
    assert inspect(postgres_database.engine).get_table_names() == []
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "FABLEAI_DATABASE_URL": postgres_database.url.render_as_string(hide_password=False), "GEMINI_API_KEY": "", "GEMINI_MODEL": ""}
    result = subprocess.run(["node", "scripts/dev.mjs", "migrate"], cwd=root, env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert postgres_database.url.password not in result.stdout + result.stderr
    postgres_database.initialize()
    with postgres_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "head")
        context = MigrationContext.configure(connection)
        assert context.get_current_revision() == "0003_credentials"
        assert compare_metadata(context, Base.metadata) == []
    assert set(inspect(postgres_database.engine).get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}


def test_project_migration_preserves_populated_conversation_history(postgres_database: Database) -> None:
    with postgres_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "0001_conversations")
    repository = ConversationRepository(postgres_database)
    conversation_id = repository.create().conversation_id
    key = uuid4()
    payload = {"instruction": "Remember the fictional paper harbor."}
    claim = repository.begin(conversation_id, key, "message", payload)
    response = MessageResponse(kind="message", conversation_id=conversation_id, message_id=uuid4(), assistant_message="Saved the fictional topic.", entries=[])
    saved = repository.finish(claim, response, snapshot=claim.snapshot)
    history = repository.messages(conversation_id, 100, 0).model_dump()
    with postgres_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "head")
        command.downgrade(migration_config(connection), "0001_conversations")
        command.upgrade(migration_config(connection), "head")
    postgres_database.initialize()
    assert repository.messages(conversation_id, 100, 0).model_dump() == history
    assert repository.begin(conversation_id, key, "message", payload) == saved


def test_project_settings_and_references_survive_reopening_independently(migrated_postgres: Database) -> None:
    database = migrated_postgres
    reference_id = str(uuid4())
    timestamp = datetime.now(UTC).isoformat()
    with Session(database.engine) as session, session.begin():
        session.add_all([ProjectRow(project_id="wowiki", display_name="WoWiki", created_at=timestamp, updated_at=timestamp, generation_settings={"category": "news", "language": "en", "count": 2}, instructions=["Keep the draft fictional."]), ProjectRow(project_id="fable-demo", display_name="Fable Demo", created_at=timestamp, updated_at=timestamp, generation_settings={"category": "announcement", "language": "tr", "count": 1}, instructions=[])])
        session.flush()
        for project_id, sample in [("wowiki", "Owned fictional archive sample."), ("fable-demo", "A separate self-authored mock announcement.")]:
            session.add(ProjectReferenceRow(project_id=project_id, id=reference_id, kind="examples", position=0, content={"id": reference_id, "text": sample}))
    reopened = Database(database.url)
    database.close()
    try:
        reopened.initialize()
        with Session(reopened.engine) as session, session.begin():
            project = session.get(ProjectRow, "wowiki")
            assert project.generation_settings["count"] == 2
            assert project.instructions == ["Keep the draft fictional."]
            project.generation_settings = {"category": "blog", "language": "en", "count": 3}
        with Session(reopened.engine) as session:
            assert session.get(ProjectRow, "fable-demo").generation_settings == {"category": "announcement", "language": "tr", "count": 1}
            assert session.get(ProjectRow, "wowiki").generation_settings["category"] == "blog"
            references = session.scalars(select(ProjectReferenceRow).order_by(ProjectReferenceRow.project_id)).all()
            assert len(references) == 2 and references[0].content != references[1].content
    finally:
        reopened.close()


def test_conversation_generation_review_revision_and_replay_survive_restart(migrated_postgres: Database) -> None:
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(side_effect=[TurnPlan(action="generate"), DraftBatch(entries=[CONTENT])])
    request = {"instruction": "Generate a fictional paper harbor news draft.", "generation_settings": {"count": 1, "tone": "neutral", "category": "news", "language": "en", "min_body_chars": 60, "max_body_chars": 400}}
    headers = {"Idempotency-Key": str(uuid4())}
    with open_client(migrated_postgres, provider) as client:
        conversation_id = client.post(BASE).json()["conversation_id"]
        url = BASE + "/" + conversation_id
        generated = client.post(url + "/messages", json=request, headers=headers)
        assert generated.status_code == 200, generated.json()
        result = generated.json()
        entry = result["entries"][0]
        assert client.patch(url + "/entries/" + entry["id"], json={"status": "accepted", "expected_version": 1}).status_code == 200
    with open_client(migrated_postgres, provider) as client:
        assert client.get(url).json()["entries"][0]["status"] == "accepted"
        assert client.post(url + "/messages", json=request, headers=headers).json() == result
        assert provider.generate.await_count == 2
        revised = {**CONTENT, "body": "In an imaginary harbor, a paper reading room now includes a lantern workshop. The fictional exhibit gives neighbors space to write and share new stories."}
        provider.generate.side_effect = [TurnPlan(action="revise"), RevisionBatch(entries=[{"entry_id": entry["id"], **revised}])]
        revision = client.post(url + "/messages", headers={"Idempotency-Key": str(uuid4())}, json={"instruction": "Revise the selected fictional draft.", "revision_targets": [{"entry_id": entry["id"], "expected_version": 1}]})
        assert revision.status_code == 200, revision.json()
        current = revision.json()["entries"][0]
        assert (current["id"], current["version"], current["body"]) == (entry["id"], 2, revised["body"])
        assert client.patch(url + "/entries/" + entry["id"], json={"status": "rejected", "expected_version": 2}).status_code == 200
        versions = client.get(url + "/entries/" + entry["id"] + "/versions").json()["items"]
        assert [(item["version"], item["status"]) for item in versions] == [(1, "accepted"), (2, "rejected")]
        assert len(client.get(url + "/messages").json()["items"]) == 4
        assert len(client.get(url + "/entries/" + entry["id"] + "/reviews").json()["items"]) == 2
        assert provider.generate.await_count == 4
        assert client.delete(url).status_code == 204
        assert client.get(url).status_code == 404


def test_concurrent_submissions_claim_a_conversation_once(migrated_postgres: Database) -> None:
    repository = ConversationRepository(migrated_postgres)
    conversation_id = repository.create().conversation_id
    barrier = Barrier(2)
    keys = [uuid4(), uuid4()]
    payload = {"instruction": "Save a fictional topic."}

    def submit(key):
        barrier.wait(timeout=5)
        try:
            return repository.begin(conversation_id, key, "message", payload)
        except RepositoryError as error:
            return error.error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, keys))
    claims = [result for result in results if isinstance(result, SubmissionClaim)]
    assert len(claims) == 1 and results.count("request_in_progress") == 1
    claim = claims[0]
    response = MessageResponse(kind="message", conversation_id=conversation_id, message_id=uuid4(), assistant_message="Saved.", entries=[])
    saved = repository.finish(claim, response, snapshot=claim.snapshot)
    assert repository.begin(conversation_id, claim.submission_id, "message", payload) == saved
    assert [message.sequence for message in repository.messages(conversation_id, 100, 0).items] == [1, 2]


def test_locked_conversation_does_not_block_a_different_conversation(migrated_postgres: Database) -> None:
    repository = ConversationRepository(migrated_postgres)
    first = repository.create().conversation_id
    second = repository.create().conversation_id
    with ThreadPoolExecutor(max_workers=1) as executor:
        with migrated_postgres.engine.begin() as connection:
            connection.execute(select(ConversationRow).where(ConversationRow.id == str(first)).with_for_update())
            result = executor.submit(repository.begin, second, uuid4(), "message", {"instruction": "Another topic."}).result(timeout=5)
            assert isinstance(result, SubmissionClaim)


def test_concurrent_expiry_recovery_records_only_one_error(migrated_postgres: Database) -> None:
    repository = ConversationRepository(migrated_postgres)
    now = datetime.now(UTC)
    repository._clock = lambda: now
    conversation_id = repository.create().conversation_id
    key = uuid4()
    payload = {"instruction": "A fictional topic."}
    claim = repository.begin(conversation_id, key, "message", payload)
    now += timedelta(seconds=PROCESSING_LEASE_SECONDS + 1)
    barrier = Barrier(2)

    def recover():
        barrier.wait(timeout=5)
        repository.recover_expired()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(recover) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    replay = repository.begin(conversation_id, key, "message", payload)
    assert isinstance(replay, SavedResponse) and replay.response.error.code == "processing_interrupted"
    late = MessageResponse(kind="message", conversation_id=conversation_id, message_id=uuid4(), assistant_message="Too late.", entries=[])
    assert repository.finish(claim, late, snapshot=claim.snapshot) == replay
    assert len(repository.messages(conversation_id, 100, 0).items) == 2
    assert repository.get(conversation_id).revision == 1


def test_failed_finish_rolls_back_references_and_preserves_recoverable_claim(migrated_postgres: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = ConversationRepository(migrated_postgres)
    conversation_id = repository.create().conversation_id
    key = uuid4()
    payload = {"instruction": "An original fictional draft."}
    claim = repository.begin(conversation_id, key, "message", payload)
    claim.snapshot.references = ReferenceCollection(examples=[ReferenceText(id=uuid4(), text="A self-authored sample.")])
    response = MessageResponse(kind="message", conversation_id=conversation_id, message_id=uuid4(), assistant_message="A proposed saved preference.", entries=[])
    with monkeypatch.context() as patch:
        patch.setattr(repository, "_save_entries", Mock(side_effect=SQLAlchemyError("simulated write failure")))
        with pytest.raises(SQLAlchemyError):
            repository.finish(claim, response, snapshot=claim.snapshot)
    detail = repository.get(conversation_id)
    assert detail.references.examples == [] and detail.processing is True and detail.revision == 0
    assert len(repository.messages(conversation_id, 100, 0).items) == 1
    failure = ErrorResponse(kind="error", conversation_id=conversation_id, message_id=uuid4(), error=ErrorInfo(code="storage_failed", message="The result was not saved."))
    saved = repository.finish(claim, failure)
    assert saved.status == 503
    assert repository.begin(conversation_id, key, "message", payload) == saved
    assert len(repository.messages(conversation_id, 100, 0).items) == 2



def test_credentials_migration_preserves_project_settings_samples_and_existing_history(postgres_database: Database) -> None:
    from app.credential_contracts import CredentialCreate
    from app.persistence.credential_repository import CredentialRepository
    from app.persistence.project_repository import ProjectRepository
    from app.project_contracts import ProjectCreate
    from test_projects import sample

    with postgres_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "0002_projects")
    projects = ProjectRepository(postgres_database)
    saved_project = projects.create(ProjectCreate(project_id="wowiki", display_name="WoWiki", generation_settings={"count": 2, "category": "news"}, references=[sample()], instructions=["Keep paragraphs compact."]))
    conversations = ConversationRepository(postgres_database)
    saved_conversation = conversations.create()
    with postgres_database.engine.begin() as connection:
        command.upgrade(migration_config(connection), "head")
    postgres_database.initialize()
    assert projects.get("wowiki") == saved_project
    assert conversations.get(saved_conversation.conversation_id) == saved_conversation
    credentials = CredentialRepository(postgres_database)
    key = credentials.issue_project("wowiki", CredentialCreate(name="Migrated integration"))
    assert credentials.authenticate(key.api_key.get_secret_value()).project_id == "wowiki"
    with postgres_database.engine.begin() as connection:
        command.downgrade(migration_config(connection), "0002_projects")
        command.upgrade(migration_config(connection), "head")
    assert projects.get("wowiki") == saved_project
    assert conversations.get(saved_conversation.conversation_id) == saved_conversation
    assert credentials.authenticate(key.api_key.get_secret_value()) is None


def test_operator_cli_credentials_survive_process_restarts(migrated_postgres: Database) -> None:
    import json
    import sys

    from app.persistence.credential_repository import CredentialRepository

    environment = {**os.environ, "FABLEAI_DATABASE_URL": migrated_postgres.url.render_as_string(hide_password=False), "GEMINI_API_KEY": "", "GEMINI_MODEL": "", "FABLEAI_LOCAL_DEMO": "false"}
    api = Path(__file__).resolve().parents[1]

    def execute(*arguments):
        result = subprocess.run([sys.executable, "-m", "app.manage_keys", *arguments], cwd=api, env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0
        assert migrated_postgres.url.password not in result.stdout + result.stderr
        assert result.stderr == ""
        return json.loads(result.stdout)

    repository = CredentialRepository(migrated_postgres)
    original = execute("issue-admin", "--name", "Isolated CLI operator")
    assert repository.authenticate(original["api_key"]).is_admin is True
    replacement = execute("rotate", original["credential_id"])
    assert repository.authenticate(original["api_key"]) is None
    assert repository.authenticate(replacement["api_key"]).is_admin is True
    assert execute("revoke", replacement["credential_id"]) == {"credential_id": replacement["credential_id"], "revoked": True}
    assert repository.authenticate(replacement["api_key"]) is None
