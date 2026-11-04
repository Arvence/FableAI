import asyncio
import hashlib
import json
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.contracts import MAX_HEADLINE_CHARS, DraftBatch, EntryContent, ErrorInfo, ErrorResponse, GenerationRequest, GenerationResponse, ReferenceCollection, ReferenceText
from app.conversation_contracts import ConversationSnapshot, GenerationPreferences
from app.generation.conversation_context import encode_drafting_context
from app.generation.generation_checks import UNSUPPORTED_TOPIC, GenerationRejected
from app.generation.synthetic_generation import SyntheticGenerationService
from app.persistence.errors import ERROR_STATUSES, RepositoryError
from app.persistence.project_repository import ProjectRepository
from app.project_contracts import ProjectDetail, ProjectGenerationRequest, ProjectGenerationSettings


REPLAY_RETENTION_SECONDS = 600
MAX_REPLAY_ENTRIES = 128


@dataclass(frozen=True)
class ProjectGenerationResult:
    response: DraftBatch | ErrorResponse
    status: int
    message: str | None = None
    replayed: bool = False


@dataclass
class _Replay:
    request_hash: str
    result: ProjectGenerationResult | None = None
    expires_at: float | None = None
    timer: asyncio.TimerHandle | None = None


class ProjectGenerationService:
    def __init__(self, repository: ProjectRepository, generator: SyntheticGenerationService, *, clock: Callable[[], float] = time.monotonic, retention_seconds: float = REPLAY_RETENTION_SECONDS, max_entries: int = MAX_REPLAY_ENTRIES) -> None:
        if retention_seconds <= 0 or max_entries < 1:
            raise ValueError("Replay retention and capacity must be positive.")
        self._repository = repository
        self._generator = generator
        self._clock = clock
        self.retention_seconds = retention_seconds
        self._max_entries = max_entries
        self._replays: dict[tuple[str, UUID], _Replay] = {}

    @staticmethod
    def _error(request_id: UUID, error: ErrorInfo) -> ProjectGenerationResult:
        return ProjectGenerationResult(ErrorResponse(kind="error", message_id=request_id, error=error), ERROR_STATUSES[error.code])

    def _expire(self, key: tuple[str, UUID], replay: _Replay) -> None:
        if self._replays.get(key) is replay:
            del self._replays[key]
        if replay.timer is not None:
            replay.timer.cancel()
            replay.timer = None

    def close(self) -> None:
        for key, replay in list(self._replays.items()):
            self._expire(key, replay)

    def _complete(self, key: tuple[str, UUID], replay: _Replay, result: ProjectGenerationResult) -> None:
        if self._replays.get(key) is replay:
            replay.result = result
            replay.expires_at = self._clock() + self.retention_seconds
            replay.timer = asyncio.get_running_loop().call_later(self.retention_seconds, self._expire, key, replay)

    async def submit(self, project_id: str, request_id: UUID, request: ProjectGenerationRequest) -> ProjectGenerationResult:
        try:
            project = await run_in_threadpool(self._repository.get, project_id)
        except RepositoryError as error:
            return self._error(request_id, error.error)
        except (SQLAlchemyError, ValidationError):
            return self._error(request_id, ErrorInfo(code="storage_failed", message="The saved project configuration could not be loaded. No generation was attempted."))
        data = {"instruction": request.instruction, "overrides": request.overrides.model_dump(exclude_none=True)}
        request_hash = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        key = (project_id, request_id)
        now = self._clock()
        for cached_key, cached in list(self._replays.items()):
            if cached.expires_at is not None and cached.expires_at <= now:
                self._expire(cached_key, cached)
        if replay := self._replays.get(key):
            if replay.request_hash != request_hash:
                return self._error(request_id, ErrorInfo(code="submission_conflict", message="This request ID was already used with different input for this project."))
            if replay.result is None:
                return self._error(request_id, ErrorInfo(code="request_in_progress", message="This project generation request is still processing. Reuse the same request ID to check its result."))
            return replace(replay.result, replayed=True)
        if len(self._replays) >= self._max_entries:
            return self._error(request_id, ErrorInfo(code="rate_limited", message="The generation replay cache is at capacity. Wait for an existing result to expire before starting another request."))
        replay = _Replay(request_hash)
        self._replays[key] = replay
        try:
            result = await self._generate(project, request_id, request)
        except asyncio.CancelledError:
            self._complete(key, replay, self._error(request_id, ErrorInfo(code="processing_interrupted", message="Generation was cancelled. No automatic retry was made. Use a new request ID only when you intend to try again.")))
            raise
        except Exception:
            result = self._error(request_id, ErrorInfo(code="generation_failed", message="Project generation could not complete. No automatic retry was made."))
        self._complete(key, replay, result)
        return result

    async def _generate(self, project: ProjectDetail, request_id: UUID, request: ProjectGenerationRequest) -> ProjectGenerationResult:
        try:
            settings = ProjectGenerationSettings.model_validate({**project.generation_settings.model_dump(), **request.overrides.model_dump(exclude_unset=True)})
        except ValidationError:
            return self._error(request_id, ErrorInfo(code="invalid_request", message="The overrides conflict with the saved project settings or exceed the supported generation limits."))
        if UNSUPPORTED_TOPIC.search(unicodedata.normalize("NFKC", request.instruction)):
            return self._error(request_id, ErrorInfo(code="invalid_request", message="Request original fictional drafts without copying, verification, impersonation, or attribution instructions."))
        preferences = {"topic": request.instruction[:MAX_HEADLINE_CHARS], **settings.model_dump(exclude={"body_length_mode"})}
        references = ReferenceCollection(examples=[ReferenceText(id=sample.id, text=sample.text) for sample in project.references])
        try:
            snapshot = ConversationSnapshot(conversation_id=request_id, preferences=GenerationPreferences(**preferences), stable_instructions=project.instructions, references=references)
            context = encode_drafting_context(snapshot, request.instruction)
            generation_request = GenerationRequest(conversation_id=request_id, **preferences)
            response = await self._generator.generate(generation_request, references, context=context, strict_body_length=settings.body_length_mode == "strict")
        except GenerationRejected as error:
            return self._error(request_id, error.error)
        if isinstance(response, ErrorResponse):
            return self._error(request_id, response.error)
        if not isinstance(response, GenerationResponse) or response.conversation_id != request_id:
            return self._error(request_id, ErrorInfo(code="generation_failed", message="The generation result did not match this request."))
        batch = DraftBatch(entries=[EntryContent.model_validate(entry.model_dump(exclude={"id", "version"})) for entry in response.entries])
        return ProjectGenerationResult(batch, 200, response.assistant_message)
