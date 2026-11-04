from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.transport.chat_api import router
from app.transport.project_api import router as project_router
from app.transport.credential_api import router as credential_router
from app.transport.authentication import CredentialMiddleware
from app.persistence.credential_repository import CredentialRepository
from app.config import Settings, load_settings
from app.contracts import SourcePermission
from app.persistence.conversation_repository import ConversationRepository
from app.persistence.errors import RepositoryError
from app.persistence.project_repository import ProjectRepository
from app.generation.conversation_service import ConversationService
from app.persistence.database import Database
from app.generation.gemini_provider import GeminiProvider
from app.generation.synthetic_generation import SyntheticGenerationService
from app.project_generation import ProjectGenerationService
from app.transport.http_safeguards import HttpSafeguardsMiddleware, error_response
from app.persistent_conversation import PersistentConversationService
from app.generation.reference_import import ReferenceImporter


def create_app(settings: Settings | None = None, *, provider: GeminiProvider | None = None, importer_factory: Callable[[Sequence[SourcePermission]], ReferenceImporter] = ReferenceImporter) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configured = settings or load_settings()
        database = Database(configured.database_url.get_secret_value() if configured.database_url else configured.database_path)
        project_generations = None
        try:
            database.initialize()
            repository = ConversationRepository(database)
            repository.recover_expired()
            application.state.repository = repository
            application.state.projects = ProjectRepository(database)
            application.state.credentials = CredentialRepository(database)
            application.state.local_demo = configured.local_demo
            selected_provider = provider or GeminiProvider(configured)
            project_generations = ProjectGenerationService(application.state.projects, SyntheticGenerationService(selected_provider))
            application.state.project_generations = project_generations
            application.state.conversations = PersistentConversationService(repository, ConversationService(selected_provider), importer_factory=importer_factory)
            yield
        finally:
            if project_generations is not None:
                project_generations.close()
            database.close()

    application = FastAPI(title=settings.app_name if settings else "FableAI", lifespan=lifespan)
    application.include_router(router)
    application.include_router(project_router)
    application.include_router(credential_router)

    application.add_middleware(CredentialMiddleware)
    application.add_middleware(HttpSafeguardsMiddleware)

    @application.exception_handler(RepositoryError)
    async def repository_error(request: Request, error: RepositoryError) -> JSONResponse:
        return error_response(error.status, error.error.code, error.error.message)

    @application.exception_handler(SQLAlchemyError)
    async def storage_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        return error_response(503, "storage_failed", "Local storage is unavailable. No successful result is being reported.")

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return error_response(422, "invalid_request", "The request contains invalid fields, identifiers, or pagination values.")

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return error_response(error.status_code, "invalid_request", "The requested endpoint or method is not available.")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
