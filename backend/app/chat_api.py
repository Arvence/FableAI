from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import UUID4

from app.api_contracts import ConversationDetail, ConversationPage, ImportResponse, ImportSubmission, MessagePage, ReviewPage, ReviewRequest, StoredEntry, VersionPage
from app.contracts import ChatTurnInput, ErrorResponse
from app.conversation_contracts import ConversationResponse
from app.conversation_repository import SavedResponse


router = APIRouter(prefix="/api/v1/conversations", responses={400: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 408: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, 500: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]
SubmissionHeader = Annotated[UUID4 | None, Header(alias="Idempotency-Key")]


def _saved_response(result: SavedResponse, submission_id: UUID) -> JSONResponse:
    return JSONResponse(status_code=result.status, content=result.response.model_dump(mode="json"), headers={"Idempotency-Key": str(submission_id)})


@router.post("", response_model=ConversationDetail, status_code=201)
def create_conversation(request: Request) -> ConversationDetail:
    return request.app.state.repository.create()


@router.get("", response_model=ConversationPage)
def list_conversations(request: Request, limit: PageLimit = 20, offset: PageOffset = 0) -> ConversationPage:
    return request.app.state.repository.list_conversations(limit, offset)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: UUID4, request: Request) -> ConversationDetail:
    return request.app.state.repository.get(conversation_id)


@router.post("/{conversation_id}/messages", response_model=ConversationResponse)
async def submit_message(conversation_id: UUID4, body: ChatTurnInput, request: Request, submission_id: SubmissionHeader = None) -> JSONResponse:
    submission_id = submission_id or uuid4()
    result = await request.app.state.conversations.submit(conversation_id, submission_id, body)
    return _saved_response(result, submission_id)


@router.get("/{conversation_id}/messages", response_model=MessagePage)
def list_messages(conversation_id: UUID4, request: Request, limit: PageLimit = 20, after: Annotated[int, Query(ge=0)] = 0) -> MessagePage:
    return request.app.state.repository.messages(conversation_id, limit, after)


@router.post("/{conversation_id}/references/import", response_model=ImportResponse | ErrorResponse)
async def import_reference(conversation_id: UUID4, body: ImportSubmission, request: Request, submission_id: SubmissionHeader = None) -> JSONResponse:
    submission_id = submission_id or uuid4()
    result = await request.app.state.conversations.import_reference(conversation_id, submission_id, body)
    return _saved_response(result, submission_id)


@router.patch("/{conversation_id}/entries/{entry_id}", response_model=StoredEntry)
def review_entry(conversation_id: UUID4, entry_id: UUID4, body: ReviewRequest, request: Request) -> StoredEntry:
    return request.app.state.repository.review(conversation_id, entry_id, body)


@router.get("/{conversation_id}/entries/{entry_id}/versions", response_model=VersionPage)
def list_versions(conversation_id: UUID4, entry_id: UUID4, request: Request, limit: PageLimit = 20, offset: PageOffset = 0) -> VersionPage:
    return request.app.state.repository.versions(conversation_id, entry_id, limit, offset)


@router.get("/{conversation_id}/entries/{entry_id}/reviews", response_model=ReviewPage)
def list_reviews(conversation_id: UUID4, entry_id: UUID4, request: Request, limit: PageLimit = 20, after: Annotated[int, Query(ge=0)] = 0) -> ReviewPage:
    return request.app.state.repository.reviews(conversation_id, entry_id, limit, after)
