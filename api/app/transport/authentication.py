from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from app.credential_contracts import Principal
from app.persistence.errors import RepositoryError
from app.project_contracts import ProjectId
from app.transport.http_safeguards import error_response


bearer = HTTPBearer(auto_error=False, scheme_name="FableAIKey")


class CredentialMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        headers = Headers(scope=scope).getlist("authorization")
        conversation_path = scope["path"] == "/api/v1/conversations" or scope["path"].startswith("/api/v1/conversations/")
        principal = None
        if not headers and request.app.state.local_demo and conversation_path:
            principal = Principal(is_local_demo=True)
        elif len(headers) == 1:
            scheme, separator, token = headers[0].partition(" ")
            if separator and scheme.lower() == "bearer":
                try:
                    principal = await run_in_threadpool(request.app.state.credentials.authenticate, token)
                except SQLAlchemyError:
                    await error_response(503, "storage_failed", "Credential verification is unavailable. No operation was started.")(scope, receive, send)
                    return
        if principal is None:
            response = error_response(401, "unauthorized", "A valid FableAI API credential is required.")
            response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)


def current_principal(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)] = None) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        raise RepositoryError("unauthorized", "A valid FableAI API credential is required.")
    return principal


def require_admin(principal: Annotated[Principal, Depends(current_principal)]) -> None:
    if not principal.is_admin:
        raise RepositoryError("access_denied", "This credential is not authorized for the requested operation.")


def require_project(project_id: ProjectId, principal: Annotated[Principal, Depends(current_principal)]) -> None:
    if not principal.is_admin and principal.project_id != project_id:
        raise RepositoryError("access_denied", "This credential is not authorized for the requested operation.")


def require_conversations(principal: Annotated[Principal, Depends(current_principal)]) -> None:
    if not principal.is_admin and not principal.is_local_demo:
        raise RepositoryError("access_denied", "This credential is not authorized for the requested operation.")
