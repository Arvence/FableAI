import asyncio
import ipaddress
import logging
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.contracts import ErrorInfo, ErrorResponse


MAX_REQUEST_BYTES = 512 * 1_024
REQUEST_BODY_TIMEOUT_SECONDS = 10.0
logger = logging.getLogger(__name__)


def error_response(status: int, code: str, message: str) -> JSONResponse:
    payload = ErrorResponse(kind="error", error=ErrorInfo(code=code, message=message))
    return JSONResponse(status_code=status, content=payload.model_dump(mode="json"))


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


class RequestRejected(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


class HttpSafeguardsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _validate_access(scope: Scope) -> None:
        request = Request(scope)
        if len(request.headers.getlist("host")) != 1:
            raise RequestRejected(403, "access_denied", "This API accepts local loopback requests only.")
        authority = request.headers["host"]
        try:
            target = urlsplit("//" + authority)
            if not authority or target.netloc != authority or target.username is not None or target.password is not None or target.port == 0 or any(ord(char) <= 32 or ord(char) == 127 for char in authority):
                raise ValueError("Invalid Host authority.")
            host = target.hostname
        except ValueError:
            raise RequestRejected(400, "invalid_request", "The request has an invalid Host header.") from None
        if not _loopback(host) or request.client is None or not _loopback(request.client.host):
            raise RequestRejected(403, "access_denied", "This API accepts local loopback requests only.")
        origins = request.headers.getlist("origin")
        allowed_origins = {f"{scope.get('scheme', 'http')}://{authority}", "http://127.0.0.1:5173", "http://localhost:5173"}
        if len(origins) > 1 or (origins and origins[0] not in allowed_origins):
            raise RequestRejected(403, "access_denied", "This origin is not allowed to access the local API.")

    @staticmethod
    def _declared_length(scope: Scope) -> int | None:
        headers = Headers(scope=scope)
        values = headers.getlist("content-length")
        if not values:
            return None
        value = values[0]
        if len(values) != 1 or len(value) > 20 or not value.isascii() or not value.isdecimal() or "transfer-encoding" in headers:
            raise RequestRejected(400, "invalid_request", "The request has invalid body-length headers.")
        length = int(value)
        if length > MAX_REQUEST_BYTES:
            raise RequestRejected(413, "invalid_request", "The request body exceeds the 512 KiB limit.")
        return length

    @staticmethod
    async def _read_body(receive: Receive, declared_length: int | None) -> bytes:
        body = bytearray()
        try:
            async with asyncio.timeout(REQUEST_BODY_TIMEOUT_SECONDS):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        raise ClientDisconnect()
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                        raise RequestRejected(413, "invalid_request", "The request body exceeds the 512 KiB limit.")
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            raise RequestRejected(408, "invalid_request", "Reading the request body timed out. No operation was started.") from None
        if declared_length is not None and len(body) != declared_length:
            raise RequestRejected(400, "invalid_request", "The request body does not match its declared length.")
        return bytes(body)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def send_private(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = [(key, value) for key, value in message.get("headers", []) if key.lower() not in {b"cache-control", b"x-content-type-options"}]
                message = {**message, "headers": [*headers, (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]}
            await send(message)

        try:
            self._validate_access(scope)
            body = await self._read_body(receive, self._declared_length(scope))
            consumed = False

            async def replay_body() -> Message:
                nonlocal consumed
                if consumed:
                    return await receive()
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}

            await self.app(scope, replay_body, send_private)
        except RequestRejected as error:
            await error_response(error.status, error.code, str(error))(scope, receive, send_private)
        except ClientDisconnect:
            return
        except Exception:
            logger.error("An unexpected local API failure occurred. Request and exception details were omitted.")
            if response_started:
                raise RuntimeError("The local API response was interrupted.") from None
            await error_response(500, "internal_error", "The request could not be completed. No successful result is being reported.")(scope, receive, send_private)
