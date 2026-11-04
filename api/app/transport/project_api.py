from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import UUID4

from app.contracts import DraftBatch, ErrorResponse
from app.transport.authentication import require_admin, require_project
from app.project_contracts import ProjectCreate, ProjectDetail, ProjectGenerationRequest, ProjectId, ProjectPage, ProjectUpdate


router = APIRouter(prefix="/api/v1/projects", responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post("", response_model=ProjectDetail, status_code=201, dependencies=[Depends(require_admin)])
def create_project(body: ProjectCreate, request: Request) -> ProjectDetail:
    return request.app.state.projects.create(body)


@router.get("", response_model=ProjectPage, dependencies=[Depends(require_admin)])
def list_projects(request: Request, limit: PageLimit = 20, offset: PageOffset = 0) -> ProjectPage:
    return request.app.state.projects.list_projects(limit, offset)


@router.get("/{project_id}", response_model=ProjectDetail, dependencies=[Depends(require_project)])
def get_project(project_id: ProjectId, request: Request) -> ProjectDetail:
    return request.app.state.projects.get(project_id)


@router.patch("/{project_id}", response_model=ProjectDetail, dependencies=[Depends(require_project)])
def update_project(project_id: ProjectId, body: ProjectUpdate, request: Request) -> ProjectDetail:
    return request.app.state.projects.update(project_id, body)


@router.get("/{project_id}/output-contract", response_model=dict[str, Any], dependencies=[Depends(require_project)])
def get_output_contract(project_id: ProjectId, request: Request) -> dict[str, Any]:
    request.app.state.projects.get(project_id)
    return DraftBatch.model_json_schema()


@router.post("/{project_id}/generations", response_model=DraftBatch | ErrorResponse, dependencies=[Depends(require_project)], responses={200: {"headers": {
    "Idempotency-Key": {"schema": {"type": "string", "format": "uuid"}},
    "X-Request-ID": {"schema": {"type": "string", "format": "uuid"}},
    "Idempotency-Scope": {"schema": {"type": "string", "const": "process"}},
    "Idempotency-Retention-Seconds": {"schema": {"type": "number"}},
    "Idempotency-Replayed": {"schema": {"type": "boolean"}},
    "X-Generation-Message": {"schema": {"type": "string"}},
}}})
async def generate_project(project_id: ProjectId, body: ProjectGenerationRequest, request: Request, request_id: Annotated[UUID4 | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    request_id = request_id or uuid4()
    service = request.app.state.project_generations
    result = await service.submit(project_id, request_id, body)
    headers = {"Idempotency-Key": str(request_id), "X-Request-ID": str(request_id), "Idempotency-Scope": "process", "Idempotency-Retention-Seconds": str(service.retention_seconds), "Idempotency-Replayed": str(result.replayed).lower()}
    if result.message is not None:
        headers["X-Generation-Message"] = result.message
    return JSONResponse(status_code=result.status, content=result.response.model_dump(mode="json"), headers=headers)
