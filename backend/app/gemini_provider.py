import asyncio
import socket
import ssl
from collections.abc import Callable
from enum import StrEnum
from typing import TypeVar

import httpx
from google import genai
from google.genai import errors, types
from pydantic import TypeAdapter

from app.config import Settings
from app.contracts import ErrorDetail, ErrorInfo


ResponseT = TypeVar("ResponseT")
MAX_OUTPUT_TOKENS = 8_192
NON_TEXT_PART_FIELDS = ("function_call", "function_response", "inline_data", "file_data", "executable_code", "code_execution_result")


class ProviderFailure(StrEnum):
    MISSING_CREDENTIALS = "missing_credentials"
    MISSING_MODEL = "missing_model"
    AUTHENTICATION_FAILED = "authentication_failed"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    UNAVAILABLE = "unavailable"
    CONNECTION_FAILED = "connection_failed"
    DNS_FAILED = "dns_failed"
    TLS_FAILED = "tls_failed"
    PROXY_FAILED = "proxy_failed"
    TRANSPORT_FAILED = "transport_failed"
    REQUEST_FAILED = "request_failed"


ERROR_MESSAGES = {
    ProviderFailure.MISSING_CREDENTIALS: "Gemini API credentials are not configured on the backend.",
    ProviderFailure.MISSING_MODEL: "A user-verified Gemini model is not configured on the backend.",
    ProviderFailure.AUTHENTICATION_FAILED: "Gemini rejected the backend credentials or access permissions.",
    ProviderFailure.RATE_LIMITED: "Gemini quota or rate limit was reached. Wait before trying again; no retry was made.",
    ProviderFailure.TIMEOUT: "The Gemini request timed out. No retry was made.",
    ProviderFailure.INVALID_RESPONSE: "Gemini returned an incomplete or invalid structured response.",
    ProviderFailure.UNAVAILABLE: "The Gemini endpoint returned a server error. Wait before sending a new message; no retry was made.",
    ProviderFailure.CONNECTION_FAILED: "The backend could not connect to Gemini. Check the backend machine's network, firewall, or proxy connection. No retry was made.",
    ProviderFailure.DNS_FAILED: "The backend could not resolve Gemini's hostname. Check the backend machine's DNS and network connection. No retry was made.",
    ProviderFailure.TLS_FAILED: "The backend could not verify Gemini's TLS certificate. Check the system clock and trusted certificates or HTTPS proxy. No retry was made.",
    ProviderFailure.PROXY_FAILED: "The backend could not establish its proxy connection to Gemini. Check the backend's proxy configuration. No retry was made.",
    ProviderFailure.TRANSPORT_FAILED: "The network exchange with Gemini failed before a usable response was received. Check the backend connection and try later. No retry was made.",
    ProviderFailure.REQUEST_FAILED: "The Gemini request could not be completed. No retry was made.",
}


class GeminiProviderError(Exception):
    def __init__(self, reason: ProviderFailure, *, http_status: int | None = None) -> None:
        self.reason = reason
        details = [ErrorDetail(field="provider_failure", message=reason.value)]
        if type(http_status) is int and 100 <= http_status <= 599:
            details.append(ErrorDetail(field="provider_http_status", message=str(http_status)))
        self.error = ErrorInfo(code="rate_limited" if reason == ProviderFailure.RATE_LIMITED else "generation_failed", message=ERROR_MESSAGES[reason], details=details)
        super().__init__(self.error.message)


class GeminiProvider:
    def __init__(self, settings: Settings, *, client_factory: Callable[..., genai.Client] | None = None) -> None:
        self._settings = settings.model_copy(deep=True)
        self._client_factory = client_factory or genai.Client

    async def generate(self, prompt: str, response_schema: type[ResponseT] | TypeAdapter[ResponseT]) -> ResponseT:
        api_key = self._settings.gemini_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise GeminiProviderError(ProviderFailure.MISSING_CREDENTIALS)
        model = self._settings.gemini_model
        if model is None or not model.strip():
            raise GeminiProviderError(ProviderFailure.MISSING_MODEL)

        adapter = response_schema if isinstance(response_schema, TypeAdapter) else TypeAdapter(response_schema)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=adapter.json_schema(),
            candidate_count=1,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            automatic_continuation=False,
        )
        http_options = types.HttpOptions(
            base_url="https://generativelanguage.googleapis.com/",
            timeout=max(1, int(self._settings.gemini_timeout_seconds * 1_000)),
            retry_options=types.HttpRetryOptions(attempts=1),
        )

        try:
            with self._client_factory(api_key=api_key.get_secret_value(), enterprise=False, http_options=http_options) as client:
                async with client.aio as async_client:
                    async with asyncio.timeout(self._settings.gemini_timeout_seconds):
                        response = await async_client.models.generate_content(model=model, contents=prompt, config=config)
            return adapter.validate_json(self._response_text(response), strict=True)
        except GeminiProviderError:
            raise
        except errors.APIError as error:
            raise GeminiProviderError(self._api_failure(error), http_status=error.code) from None
        except (TimeoutError, httpx.TimeoutException):
            raise GeminiProviderError(ProviderFailure.TIMEOUT) from None
        except httpx.RequestError as error:
            raise GeminiProviderError(self._transport_failure(error)) from None
        except (ValueError, TypeError, AttributeError):
            raise GeminiProviderError(ProviderFailure.INVALID_RESPONSE) from None
        except Exception:
            raise GeminiProviderError(ProviderFailure.REQUEST_FAILED) from None

    @staticmethod
    def _api_failure(error: errors.APIError) -> ProviderFailure:
        status = error.code
        if status in {401, 403}:
            return ProviderFailure.AUTHENTICATION_FAILED
        if status == 400 and isinstance(error.details, dict):
            payload = error.details.get("error", error.details)
            details = payload.get("details") if isinstance(payload, dict) else None
            if isinstance(details, list) and any(isinstance(detail, dict) and detail.get("reason") == "API_KEY_INVALID" for detail in details):
                return ProviderFailure.AUTHENTICATION_FAILED
        if status == 429:
            return ProviderFailure.RATE_LIMITED
        if status in {408, 504}:
            return ProviderFailure.TIMEOUT
        if 500 <= status < 600:
            return ProviderFailure.UNAVAILABLE
        return ProviderFailure.REQUEST_FAILED

    @staticmethod
    def _transport_failure(error: httpx.RequestError) -> ProviderFailure:
        cause: BaseException | None = error
        seen = set()
        while cause is not None and id(cause) not in seen and len(seen) < 10:
            seen.add(id(cause))
            if isinstance(cause, ssl.SSLCertVerificationError):
                return ProviderFailure.TLS_FAILED
            if isinstance(cause, socket.gaierror):
                return ProviderFailure.DNS_FAILED
            cause = cause.__cause__ or cause.__context__
        if isinstance(error, httpx.ProxyError):
            return ProviderFailure.PROXY_FAILED
        if isinstance(error, httpx.ConnectError):
            return ProviderFailure.CONNECTION_FAILED
        return ProviderFailure.TRANSPORT_FAILED

    @staticmethod
    def _response_text(response: types.GenerateContentResponse) -> str:
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise GeminiProviderError(ProviderFailure.INVALID_RESPONSE)
        if not response.candidates or len(response.candidates) != 1:
            raise GeminiProviderError(ProviderFailure.INVALID_RESPONSE)
        candidate = response.candidates[0]
        if candidate.finish_reason != types.FinishReason.STOP or not candidate.content or not candidate.content.parts:
            raise GeminiProviderError(ProviderFailure.INVALID_RESPONSE)

        text = ""
        for part in candidate.content.parts:
            if part.thought:
                continue
            if part.text is None or any(getattr(part, field) is not None for field in NON_TEXT_PART_FIELDS):
                raise GeminiProviderError(ProviderFailure.INVALID_RESPONSE)
            text += part.text
        if not text.strip():
            raise GeminiProviderError(ProviderFailure.INVALID_RESPONSE)
        return text
