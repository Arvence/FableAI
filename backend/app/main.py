import ipaddress
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from app.chat_api import router
from app.config import Settings, load_settings
from app.contracts import ErrorInfo, ErrorResponse, SourcePermission
from app.conversation_repository import ConversationRepository, RepositoryError
from app.conversation_service import ConversationService
from app.database import Database
from app.gemini_provider import GeminiProvider
from app.persistent_conversation import PersistentConversationService
from app.reference_import import ReferenceImporter


def _error(status: int, code: str, message: str) -> JSONResponse:
    payload = ErrorResponse(kind="error", error=ErrorInfo(code=code, message=message))
    return JSONResponse(status_code=status, content=payload.model_dump(mode="json"))


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


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

    @application.middleware("http")
    async def local_access(request: Request, call_next: Callable) -> object:
        if not _loopback(request.url.hostname) or request.client is None or not _loopback(request.client.host):
            return _error(403, "access_denied", "This API accepts local loopback requests only.")
        origin = request.headers.get("origin")
        allowed_origins = {f"{request.url.scheme}://{request.url.netloc}", "http://127.0.0.1:5173", "http://localhost:5173"}
        if origin and origin not in allowed_origins:
            return _error(403, "access_denied", "This origin is not allowed to access the local API.")
        return await call_next(request)

    @application.exception_handler(RepositoryError)
    async def repository_error(request: Request, error: RepositoryError) -> JSONResponse:
        return _error(error.status, error.error.code, error.error.message)

    @application.exception_handler(SQLAlchemyError)
    async def storage_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        return _error(503, "storage_failed", "Local storage is unavailable. No successful result is being reported.")

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        return _error(422, "invalid_request", "The request contains invalid fields, identifiers, or pagination values.")

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        return _error(error.status_code, "invalid_request", "The requested endpoint or method is not available.")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
