import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api_contracts import ConversationDetail, ConversationPage, ConversationSummary, ImportResponse, MessagePage, ReviewDecision, ReviewPage, ReviewRequest, StoredEntry, StoredMessage, VersionPage
from app.contracts import MAX_CONTEXT_CHARS, MAX_CONTEXT_MESSAGES, ChatMessage, ErrorCode, ErrorInfo, ErrorResponse, GeneratedEntry, ReferenceCollection
from app.conversation_contracts import ConversationResponse, ConversationSnapshot
from app.database import Database
from app.storage_models import ConversationRow, EntryRow, EntryVersionRow, MessageRow, ReferenceRow, ReviewRow, SubmissionRow


PROCESSING_LEASE_SECONDS = 180
RESPONSE_ADAPTER = TypeAdapter(ConversationResponse | ImportResponse)
ERROR_STATUSES = {"invalid_request": 422, "conversation_not_found": 404, "entry_not_found": 404, "reference_not_found": 404, "version_conflict": 409, "request_in_progress": 409, "submission_conflict": 409, "processing_interrupted": 409, "reference_import_failed": 422, "generation_failed": 502, "rate_limited": 429, "storage_failed": 503, "access_denied": 403, "internal_error": 500}


class RepositoryError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.error = ErrorInfo(code=code, message=message)
        self.status = ERROR_STATUSES[code]
        super().__init__(message)


@dataclass(frozen=True)
class SavedResponse:
    response: ConversationResponse | ImportResponse
    status: int


@dataclass(frozen=True)
class SubmissionClaim:
    conversation_id: UUID
    submission_id: UUID
    revision: int
    snapshot: ConversationSnapshot


class ConversationRepository:
    def __init__(self, database: Database, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.database = database
        self._clock = clock

    @contextmanager
    def _write(self) -> Iterator[Session]:
        with self.database.engine.connect().execution_options(write_transaction=True) as connection, Session(bind=connection) as session, session.begin():
            yield session

    @staticmethod
    def _conversation(session: Session, conversation_id: UUID) -> ConversationRow:
        row = session.get(ConversationRow, str(conversation_id))
        if row is None:
            raise RepositoryError("conversation_not_found", "The conversation does not exist.")
        return row

    @staticmethod
    def _entry(session: Session, conversation_id: UUID, entry_id: UUID) -> EntryRow:
        row = session.get(EntryRow, str(entry_id))
        if row is None or row.conversation_id != str(conversation_id):
            raise RepositoryError("entry_not_found", "The entry does not exist in this conversation.")
        return row

    @staticmethod
    def _summary(row: ConversationRow) -> ConversationSummary:
        return ConversationSummary(conversation_id=row.id, created_at=datetime.fromisoformat(row.created_at), updated_at=datetime.fromisoformat(row.updated_at), revision=row.revision, processing=row.processing_id is not None)

    @staticmethod
    def _cached(row: SubmissionRow) -> SavedResponse:
        return SavedResponse(RESPONSE_ADAPTER.validate_python(row.response), row.http_status)

    def create(self) -> ConversationDetail:
        timestamp = self._clock().isoformat()
        with self._write() as session:
            row = ConversationRow(id=str(uuid4()), created_at=timestamp, updated_at=timestamp, preferences={}, stable_instructions=[])
            session.add(row)
            session.flush()
            conversation_id = UUID(row.id)
        return self.get(conversation_id)

    def delete(self, conversation_id: UUID) -> None:
        with self._write() as session:
            row = self._conversation(session, conversation_id)
            self._recover(session, row)
            if row.processing_id is not None:
                raise RepositoryError("request_in_progress", "Wait for the current conversation request to finish before deleting this chat.")
            entry_ids = select(EntryRow.id).where(EntryRow.conversation_id == row.id)
            session.execute(delete(ReviewRow).where(ReviewRow.entry_id.in_(entry_ids)))
            session.execute(delete(EntryVersionRow).where(EntryVersionRow.entry_id.in_(entry_ids)))
            for model in (EntryRow, ReferenceRow, MessageRow, SubmissionRow):
                session.execute(delete(model).where(model.conversation_id == row.id))
            session.delete(row)

    def list_conversations(self, limit: int, offset: int) -> ConversationPage:
        with Session(self.database.engine) as session:
            rows = session.scalars(select(ConversationRow).order_by(ConversationRow.created_at.desc(), ConversationRow.id).offset(offset).limit(limit + 1)).all()
            return ConversationPage(items=[self._summary(row) for row in rows[:limit]], next_offset=offset + limit if len(rows) > limit else None)

    @staticmethod
    def _references(session: Session, conversation_id: str) -> ReferenceCollection:
        data = {"examples": [], "headlines": [], "urls": []}
        for row in session.scalars(select(ReferenceRow).where(ReferenceRow.conversation_id == conversation_id).order_by(ReferenceRow.position)):
            data[row.kind].append(row.content)
        return ReferenceCollection.model_validate(data)

    def _snapshot(self, session: Session, row: ConversationRow) -> ConversationSnapshot:
        references = self._references(session, row.id)
        messages = list(reversed(session.scalars(select(MessageRow).where(MessageRow.conversation_id == row.id).order_by(MessageRow.sequence.desc()).limit(MAX_CONTEXT_MESSAGES)).all()))
        while messages and sum(len(message.content) for message in messages) + references.character_count > MAX_CONTEXT_CHARS:
            messages.pop(0)
        versions = session.scalars(select(EntryVersionRow).join(EntryRow, (EntryVersionRow.entry_id == EntryRow.id) & (EntryVersionRow.version == EntryRow.current_version)).where(EntryRow.conversation_id == row.id).order_by(EntryRow.position)).all()
        return ConversationSnapshot(
            conversation_id=row.id,
            preferences=row.preferences,
            stable_instructions=row.stable_instructions,
            pending=row.pending,
            references=references,
            messages=[ChatMessage(conversation_id=row.id, message_id=message.id, role=message.role, content=message.content) for message in messages],
            active_entries=[GeneratedEntry.model_validate(version.content) for version in versions],
        )

    def get(self, conversation_id: UUID) -> ConversationDetail:
        with Session(self.database.engine) as session:
            row = self._conversation(session, conversation_id)
            snapshot = self._snapshot(session, row)
            statuses = {entry.id: entry.status for entry in session.scalars(select(EntryRow).where(EntryRow.conversation_id == row.id))}
            return ConversationDetail(**self._summary(row).model_dump(), preferences=snapshot.preferences, stable_instructions=snapshot.stable_instructions, pending=snapshot.pending, references=snapshot.references, entries=[StoredEntry.model_validate({**entry.model_dump(), "status": statuses[str(entry.id)]}) for entry in snapshot.active_entries])

    def messages(self, conversation_id: UUID, limit: int, after: int) -> MessagePage:
        with Session(self.database.engine) as session:
            self._conversation(session, conversation_id)
            rows = session.scalars(select(MessageRow).where(MessageRow.conversation_id == str(conversation_id), MessageRow.sequence > after).order_by(MessageRow.sequence).limit(limit + 1)).all()
            items = [StoredMessage(message_id=row.id, submission_id=row.submission_id, sequence=row.sequence, role=row.role, content=row.content, created_at=datetime.fromisoformat(row.created_at), request=row.payload if row.role == "user" else None, response=row.payload if row.role == "assistant" else None) for row in rows[:limit]]
            return MessagePage(items=items, next_after=items[-1].sequence if len(rows) > limit else None)

    def _append_message(self, session: Session, row: ConversationRow, submission: SubmissionRow, role: str, content: str, payload: dict, message_id: UUID | None = None) -> None:
        row.message_sequence += 1
        session.add(MessageRow(id=str(message_id or uuid4()), conversation_id=row.id, submission_id=submission.id, sequence=row.message_sequence, role=role, content=content, payload=payload, created_at=self._clock().isoformat()))

    def _complete(self, session: Session, row: ConversationRow, submission: SubmissionRow, response: ConversationResponse | ImportResponse, status: int) -> SavedResponse:
        if submission.operation == "message":
            content = response.error.message if isinstance(response, ErrorResponse) else response.assistant_message
            self._append_message(session, row, submission, "assistant", content, response.model_dump(mode="json"), response.message_id)
        submission.status = "completed"
        submission.response = response.model_dump(mode="json")
        submission.http_status = status
        row.processing_id = None
        row.revision += 1
        row.updated_at = self._clock().isoformat()
        return SavedResponse(response, status)

    def _recover(self, session: Session, row: ConversationRow) -> None:
        if row.processing_id is None:
            return
        submission = session.get(SubmissionRow, (row.id, row.processing_id))
        if submission.expires_at <= self._clock().timestamp():
            error = ErrorResponse(kind="error", conversation_id=row.id, message_id=uuid4(), error=ErrorInfo(code="processing_interrupted", message="Processing was interrupted or exceeded its time limit. Submit a new request ID to retry."))
            self._complete(session, row, submission, error, 409)
            session.flush()

    def recover_expired(self) -> None:
        with self._write() as session:
            for row in session.scalars(select(ConversationRow).where(ConversationRow.processing_id.is_not(None))):
                self._recover(session, row)

    def begin(self, conversation_id: UUID, submission_id: UUID, operation: str, data: dict) -> SubmissionClaim | SavedResponse:
        fingerprint = hashlib.sha256(json.dumps({"operation": operation, "data": data}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        with self._write() as session:
            row = self._conversation(session, conversation_id)
            self._recover(session, row)
            existing = session.get(SubmissionRow, (str(conversation_id), str(submission_id)))
            if existing is not None:
                if existing.request_hash != fingerprint:
                    raise RepositoryError("submission_conflict", "This submission ID was already used for different input.")
                if existing.status == "completed":
                    return self._cached(existing)
                raise RepositoryError("request_in_progress", "This submission is already processing.")
            if row.processing_id is not None:
                raise RepositoryError("request_in_progress", "Another request is processing in this conversation.")
            snapshot = self._snapshot(session, row)
            if operation == "import" and not any(str(reference.id) == data["reference_id"] for reference in snapshot.references.urls):
                raise RepositoryError("reference_not_found", "The URL reference does not exist in this conversation.")
            submission = SubmissionRow(conversation_id=row.id, id=str(submission_id), operation=operation, request_hash=fingerprint, status="processing", started_at=self._clock().isoformat(), expires_at=self._clock().timestamp() + PROCESSING_LEASE_SECONDS)
            session.add(submission)
            session.flush()
            row.processing_id = str(submission_id)
            row.updated_at = self._clock().isoformat()
            if operation == "message":
                self._append_message(session, row, submission, "user", data["instruction"], data)
            return SubmissionClaim(conversation_id, submission_id, row.revision, snapshot)

    def _save_references(self, session: Session, conversation_id: str, references: ReferenceCollection) -> None:
        references = ReferenceCollection.model_validate(references.model_dump())
        existing = {item.id: item for item in session.scalars(select(ReferenceRow).where(ReferenceRow.conversation_id == conversation_id))}
        seen = set()
        for kind in ("examples", "headlines", "urls"):
            for position, reference in enumerate(getattr(references, kind)):
                reference_id = str(reference.id)
                seen.add(reference_id)
                row = existing.get(reference_id)
                if row is None:
                    row = ReferenceRow(id=reference_id, conversation_id=conversation_id, kind=kind, position=position)
                    session.add(row)
                elif row.kind != kind:
                    raise RepositoryError("storage_failed", "A reference cannot change its type.")
                row.content = reference.model_dump(mode="json")
                row.import_status = reference.import_status if kind == "urls" else None
        if not existing.keys() <= seen:
            raise RepositoryError("storage_failed", "A conversation update cannot discard saved references.")

    def _save_entries(self, session: Session, conversation_id: str, entries: list[GeneratedEntry]) -> None:
        existing = {entry.id: entry for entry in session.scalars(select(EntryRow).where(EntryRow.conversation_id == conversation_id))}
        if not existing.keys() <= {str(entry.id) for entry in entries}:
            raise RepositoryError("storage_failed", "A conversation update cannot discard saved entries.")
        for position, entry in enumerate(entries):
            row = existing.get(str(entry.id))
            if row is None:
                if entry.version != 1:
                    raise RepositoryError("version_conflict", "A new entry must start at version one.")
                row = EntryRow(id=str(entry.id), conversation_id=conversation_id, position=position, current_version=1, status="draft")
                session.add(row)
                session.flush()
            else:
                previous = session.get(EntryVersionRow, (row.id, row.current_version))
                if row.position != position:
                    raise RepositoryError("storage_failed", "Saved entry ordering cannot change.")
                if entry.version == row.current_version and entry.model_dump(mode="json") == previous.content:
                    continue
                if entry.version != row.current_version + 1:
                    raise RepositoryError("version_conflict", "A revision must increment the current entry version.")
                row.current_version = entry.version
                row.status = "draft"
            session.add(EntryVersionRow(entry_id=row.id, version=entry.version, content=entry.model_dump(mode="json"), created_at=self._clock().isoformat()))

    def finish(self, claim: SubmissionClaim, response: ConversationResponse | ImportResponse, *, snapshot: ConversationSnapshot | None = None, references: ReferenceCollection | None = None) -> SavedResponse:
        response = RESPONSE_ADAPTER.validate_python(response.model_dump())
        if response.conversation_id != claim.conversation_id:
            raise RepositoryError("storage_failed", "The result belongs to a different conversation.")
        if snapshot is not None:
            snapshot = ConversationSnapshot.model_validate(snapshot.model_dump())
            if snapshot.conversation_id != claim.conversation_id or isinstance(response, ErrorResponse):
                raise RepositoryError("storage_failed", "The conversation result is inconsistent.")
        with self._write() as session:
            row = self._conversation(session, claim.conversation_id)
            self._recover(session, row)
            submission = session.get(SubmissionRow, (str(claim.conversation_id), str(claim.submission_id)))
            if submission.status == "completed":
                return self._cached(submission)
            if row.processing_id != str(claim.submission_id) or row.revision != claim.revision:
                raise RepositoryError("version_conflict", "The conversation changed while this request was processing.")
            if snapshot is not None:
                row.preferences = snapshot.preferences.model_dump(mode="json")
                row.stable_instructions = snapshot.stable_instructions
                row.pending = snapshot.pending.model_dump(mode="json") if snapshot.pending else None
                self._save_references(session, row.id, snapshot.references)
                self._save_entries(session, row.id, snapshot.active_entries)
            elif references is not None:
                self._save_references(session, row.id, references)
            status = ERROR_STATUSES[response.error.code] if isinstance(response, ErrorResponse) else 200
            return self._complete(session, row, submission, response, status)

    def review(self, conversation_id: UUID, entry_id: UUID, request: ReviewRequest) -> StoredEntry:
        with self._write() as session:
            conversation = self._conversation(session, conversation_id)
            self._recover(session, conversation)
            if conversation.processing_id is not None:
                raise RepositoryError("request_in_progress", "Wait for the current conversation request to finish before reviewing an entry.")
            entry = self._entry(session, conversation_id, entry_id)
            if entry.current_version != request.expected_version:
                raise RepositoryError("version_conflict", "Review the current entry version.")
            if entry.status != request.status:
                entry.status = request.status
                session.add(ReviewRow(entry_id=entry.id, version=entry.current_version, status=request.status, created_at=self._clock().isoformat()))
                conversation.revision += 1
                conversation.updated_at = self._clock().isoformat()
            content = session.get(EntryVersionRow, (entry.id, entry.current_version)).content
            return StoredEntry.model_validate({**content, "status": entry.status})

    def versions(self, conversation_id: UUID, entry_id: UUID, limit: int, offset: int) -> VersionPage:
        with Session(self.database.engine) as session:
            self._conversation(session, conversation_id)
            entry = self._entry(session, conversation_id, entry_id)
            rows = session.scalars(select(EntryVersionRow).where(EntryVersionRow.entry_id == entry.id).order_by(EntryVersionRow.version).offset(offset).limit(limit + 1)).all()
            items = []
            for row in rows[:limit]:
                review = session.scalar(select(ReviewRow).where(ReviewRow.entry_id == entry.id, ReviewRow.version == row.version).order_by(ReviewRow.id.desc()).limit(1))
                items.append(StoredEntry.model_validate({**row.content, "status": review.status if review else "draft"}))
            return VersionPage(items=items, next_offset=offset + limit if len(rows) > limit else None)

    def reviews(self, conversation_id: UUID, entry_id: UUID, limit: int, after: int) -> ReviewPage:
        with Session(self.database.engine) as session:
            self._conversation(session, conversation_id)
            self._entry(session, conversation_id, entry_id)
            rows = session.scalars(select(ReviewRow).where(ReviewRow.entry_id == str(entry_id), ReviewRow.id > after).order_by(ReviewRow.id).limit(limit + 1)).all()
            items = [ReviewDecision(decision_id=row.id, entry_id=row.entry_id, version=row.version, status=row.status, created_at=datetime.fromisoformat(row.created_at)) for row in rows[:limit]]
            return ReviewPage(items=items, next_after=items[-1].decision_id if len(rows) > limit else None)
