from typing import Annotated, Any

from fastapi import APIRouter, Query, Request

from app.contracts import DraftBatch, ErrorResponse
from app.project_contracts import ProjectCreate, ProjectDetail, ProjectId, ProjectPage, ProjectUpdate


router = APIRouter(prefix="/api/v1/projects", responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.post("", response_model=ProjectDetail, status_code=201)
def create_project(body: ProjectCreate, request: Request) -> ProjectDetail:
    return request.app.state.projects.create(body)


@router.get("", response_model=ProjectPage)
def list_projects(request: Request, limit: PageLimit = 20, offset: PageOffset = 0) -> ProjectPage:
    return request.app.state.projects.list_projects(limit, offset)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: ProjectId, request: Request) -> ProjectDetail:
    return request.app.state.projects.get(project_id)


@router.patch("/{project_id}", response_model=ProjectDetail)
def update_project(project_id: ProjectId, body: ProjectUpdate, request: Request) -> ProjectDetail:
    return request.app.state.projects.update(project_id, body)


@router.get("/{project_id}/output-contract", response_model=dict[str, Any])
def get_output_contract(project_id: ProjectId, request: Request) -> dict[str, Any]:
    request.app.state.projects.get(project_id)
    return DraftBatch.model_json_schema()
