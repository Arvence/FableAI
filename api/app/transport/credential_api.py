from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import UUID4

from app.contracts import ErrorResponse
from app.credential_contracts import CredentialCreate, CredentialIssued, CredentialPage
from app.project_contracts import ProjectId
from app.transport.authentication import require_admin


router = APIRouter(prefix="/api/v1/projects/{project_id}/keys", dependencies=[Depends(require_admin)], responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})


@router.post("", response_model=CredentialIssued, status_code=201)
def issue_project_key(project_id: ProjectId, body: CredentialCreate, request: Request) -> CredentialIssued:
    return request.app.state.credentials.issue_project(project_id, body)


@router.get("", response_model=CredentialPage)
def list_project_keys(project_id: ProjectId, request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 20, offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0) -> CredentialPage:
    return request.app.state.credentials.list_project(project_id, limit, offset)


@router.delete("/{credential_id}", status_code=204, response_class=Response)
def revoke_project_key(project_id: ProjectId, credential_id: UUID4, request: Request) -> Response:
    request.app.state.credentials.revoke(credential_id, project_id)
    return Response(status_code=204)


@router.post("/{credential_id}/rotate", response_model=CredentialIssued, status_code=201)
def rotate_project_key(project_id: ProjectId, credential_id: UUID4, request: Request) -> CredentialIssued:
    return request.app.state.credentials.rotate(credential_id, project_id)
