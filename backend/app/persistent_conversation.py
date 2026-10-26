import asyncio
from collections.abc import Callable, Sequence
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.api_contracts import ImportResponse, ImportSubmission
from app.contracts import ChatTurnInput, ChatTurnRequest, ErrorInfo, ErrorResponse, SourcePermission
from app.conversation_contracts import ConversationTurnResult
from app.conversation_repository import ConversationRepository, RepositoryError, SavedResponse, SubmissionClaim
from app.conversation_service import ConversationService
from app.reference_import import ReferenceImporter
from app.reference_management import ReferenceImportRequest


class PersistentConversationService:
    def __init__(self, repository: ConversationRepository, orchestrator: ConversationService, *, importer_factory: Callable[[Sequence[SourcePermission]], ReferenceImporter] = ReferenceImporter) -> None:
        self._repository = repository
        self._orchestrator = orchestrator
        self._importer_factory = importer_factory

    @staticmethod
    def _error(claim: SubmissionClaim, code: str, message: str) -> ErrorResponse:
        return ErrorResponse(kind="error", conversation_id=claim.conversation_id, message_id=uuid4(), error=ErrorInfo(code=code, message=message))

    async def _save(self, claim: SubmissionClaim, response: object, **updates: object) -> SavedResponse:
        try:
            return await run_in_threadpool(self._repository.finish, claim, response, **updates)
        except (SQLAlchemyError, ValidationError, RepositoryError):
            failure = self._error(claim, "storage_failed", "The result could not be saved. No generated result was committed.")
            try:
                return await run_in_threadpool(self._repository.finish, claim, failure)
            except (SQLAlchemyError, ValidationError, RepositoryError):
                raise RepositoryError("storage_failed", "Storage is unavailable. Retry with the same submission ID after storage recovers.") from None

    async def submit(self, conversation_id: UUID, submission_id: UUID, request: ChatTurnInput) -> SavedResponse:
        data = request.model_dump(mode="json")
        settings = request.generation_settings.model_dump(exclude_none=True) if request.generation_settings is not None else {}
        if settings:
            data["generation_settings"] = settings
        else:
            data.pop("generation_settings")
        claim = await run_in_threadpool(self._repository.begin, conversation_id, submission_id, "message", data)
        if isinstance(claim, SavedResponse):
            return claim
        try:
            turn = ChatTurnRequest(conversation_id=conversation_id, **request.model_dump())
            result = await self._orchestrator.handle_turn(turn, claim.snapshot)
            result = ConversationTurnResult.model_validate(result.model_dump())
            if result.response.conversation_id != conversation_id:
                raise ValueError("Conversation mismatch.")
            if result.response.kind != "error" and result.snapshot is None:
                raise ValueError("Missing successful snapshot.")
        except asyncio.CancelledError:
            await asyncio.shield(self._save(claim, self._error(claim, "processing_interrupted", "Processing was cancelled. Submit a new request ID to retry.")))
            raise
        except Exception:
            return await self._save(claim, self._error(claim, "generation_failed", "The conversation could not complete this request. No drafts were saved."))
        snapshot = result.snapshot if result.response.kind != "error" else None
        return await self._save(claim, result.response, snapshot=snapshot)

    async def import_reference(self, conversation_id: UUID, submission_id: UUID, request: ImportSubmission) -> SavedResponse:
        claim = await run_in_threadpool(self._repository.begin, conversation_id, submission_id, "import", request.model_dump(mode="json"))
        if isinstance(claim, SavedResponse):
            return claim
        try:
            importer = self._importer_factory(request.permissions)
            references = await importer.import_reference(claim.snapshot.references, ReferenceImportRequest(reference_id=request.reference_id, confirmed=request.confirmed))
            reference = next(item for item in references.urls if item.id == request.reference_id)
            response = ErrorResponse(kind="error", conversation_id=conversation_id, message_id=uuid4(), error=reference.error) if reference.error else ImportResponse(conversation_id=conversation_id, references=references)
        except asyncio.CancelledError:
            await asyncio.shield(self._save(claim, self._error(claim, "processing_interrupted", "The reference import was cancelled. Submit a new request ID to retry.")))
            raise
        except Exception:
            return await self._save(claim, self._error(claim, "reference_import_failed", "The reference import could not complete. Paste authorized text instead."))
        return await self._save(claim, response, references=references)
