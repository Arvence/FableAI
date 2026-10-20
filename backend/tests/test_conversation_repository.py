import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.contracts import ChatTurnInput, MessageResponse
from app.conversation_repository import PROCESSING_LEASE_SECONDS, ConversationRepository, RepositoryError, SavedResponse
from app.conversation_service import ConversationService
from app.database import Database
from app.persistent_conversation import PersistentConversationService


@pytest.fixture
def repository(tmp_path: Path) -> ConversationRepository:
    database = Database(tmp_path / "repository.sqlite3")
    database.initialize()
    try:
        yield ConversationRepository(database)
    finally:
        database.close()


def test_schema_uses_foreign_keys_wal_and_unique_message_positions(repository: ConversationRepository) -> None:
    schema = inspect(repository.database.engine)
    assert set(schema.get_table_names()) == {"conversations", "submissions", "chat_messages", "reference_material", "generated_entries", "entry_versions", "entry_reviews"}
    with repository.database.engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
    assert any(constraint["column_names"] == ["conversation_id", "sequence"] for constraint in schema.get_unique_constraints("chat_messages"))
    with pytest.raises(IntegrityError):
        with repository.database.engine.begin() as connection:
            connection.execute(text("INSERT INTO generated_entries (id, conversation_id, position, current_version, status) VALUES (:id, :conversation, 0, 1, 'draft')"), {"id": str(uuid4()), "conversation": str(uuid4())})


def test_late_result_cannot_overwrite_an_expired_submission(repository: ConversationRepository) -> None:
    now = datetime.now(UTC)
    repository._clock = lambda: now
    conversation_id = repository.create().conversation_id
    request = ChatTurnInput(instruction="Save a preference.")
    key = uuid4()
    claim = repository.begin(conversation_id, key, "message", request.model_dump(mode="json"))
    now += timedelta(seconds=PROCESSING_LEASE_SECONDS + 1)
    late = MessageResponse(kind="message", conversation_id=conversation_id, message_id=uuid4(), assistant_message="Saved the preference.", entries=[])
    result = repository.finish(claim, late, snapshot=claim.snapshot)
    assert result.status == 409 and result.response.error.code == "processing_interrupted"
    assert len(repository.messages(conversation_id, 100, 0).items) == 2
    assert repository.get(conversation_id).processing is False
    assert repository.begin(conversation_id, key, "message", request.model_dump(mode="json")) == result


def test_unexpired_claim_survives_reopening_and_blocks_a_second_processor(repository: ConversationRepository) -> None:
    conversation_id = repository.create().conversation_id
    request = ChatTurnInput(instruction="Save a preference.")
    key = uuid4()
    repository.begin(conversation_id, key, "message", request.model_dump(mode="json"))
    another = Database(repository.database.path)
    another.initialize()
    try:
        restored = ConversationRepository(another)
        restored.recover_expired()
        with pytest.raises(RepositoryError) as failure:
            restored.begin(conversation_id, key, "message", request.model_dump(mode="json"))
        assert failure.value.error.code == "request_in_progress"
        assert len(restored.messages(conversation_id, 100, 0).items) == 1
    finally:
        another.close()


def test_idempotency_keys_are_scoped_to_the_conversation(repository: ConversationRepository) -> None:
    first, second = repository.create().conversation_id, repository.create().conversation_id
    key = uuid4()
    request = ChatTurnInput(instruction="Save my preference.")
    for conversation_id in (first, second):
        claim = repository.begin(conversation_id, key, "message", request.model_dump(mode="json"))
        result = MessageResponse(kind="message", conversation_id=conversation_id, message_id=uuid4(), assistant_message="Saved.", entries=[])
        repository.finish(claim, result, snapshot=claim.snapshot)
    assert len(repository.messages(first, 100, 0).items) == len(repository.messages(second, 100, 0).items) == 2


def test_cancellation_finishes_submission_with_error_and_releases_conversation(repository: ConversationRepository) -> None:
    conversation_id = repository.create().conversation_id
    orchestrator = Mock(spec=ConversationService)
    orchestrator.handle_turn = AsyncMock(side_effect=asyncio.CancelledError())
    service = PersistentConversationService(repository, orchestrator)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.submit(conversation_id, uuid4(), ChatTurnInput(instruction="Save this preference.")))
    messages = repository.messages(conversation_id, 100, 0).items
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[-1].response.kind == "error"
    assert messages[-1].response.error.code == "processing_interrupted"
    assert repository.get(conversation_id).processing is False


def test_unexpected_orchestrator_failure_is_sanitized_and_durable(repository: ConversationRepository) -> None:
    conversation_id = repository.create().conversation_id
    orchestrator = Mock(spec=ConversationService)
    orchestrator.handle_turn = AsyncMock(side_effect=RuntimeError("private credential-like detail"))
    service = PersistentConversationService(repository, orchestrator)
    request = ChatTurnInput(instruction="Save a preference.")
    key = uuid4()
    response = asyncio.run(service.submit(conversation_id, key, request))
    assert response.status == 502 and response.response.kind == "error"
    assert "private credential-like detail" not in response.response.model_dump_json()
    replay = asyncio.run(service.submit(conversation_id, key, request))
    assert isinstance(replay, SavedResponse) and replay == response
    orchestrator.handle_turn.assert_awaited_once()
