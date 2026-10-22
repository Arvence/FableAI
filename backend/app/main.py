from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.chat_api import router
from app.config import Settings, load_settings
from app.contracts import SourcePermission
from app.conversation_repository import ConversationRepository, RepositoryError
from app.conversation_service import ConversationService
from app.database import Database
from app.gemini_provider import GeminiProvider
from app.http_safeguards import HttpSafeguardsMiddleware, error_response
from app.persistent_conversation import PersistentConversationService
from app.reference_import import ReferenceImporter


def create_app(settings: Settings | None = None, *, provider: GeminiProvider | None = None, importer_factory: Callable[[Sequence[SourcePermission]], ReferenceImporter] = ReferenceImporter) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configured = settings or load_settings()
        database = Database(configured.database_path)
        try:
            database.initialize()
            repository = ConversationRepository(database)
            repository.recover_expired()
            application.state.repository = repository
            application.state.conversations = PersistentConversationService(repository, ConversationService(provider or GeminiProvider(configured)), importer_factory=importer_factory)
            yield
        finally:
            database.close()

    application = FastAPI(title=settings.app_name if settings else "FableAI", lifespan=lifespan)
    application.include_router(router)

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
