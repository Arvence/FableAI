import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from uvicorn import Config

from app.config import Settings
from app.conversation_contracts import TurnPlan
from app.gemini_provider import GeminiProvider
from app.http_safeguards import MAX_REQUEST_BYTES, HttpSafeguardsMiddleware
from app.main import create_app


BASE = "/api/v1/conversations"


def scope(**changes: object) -> dict:
    return {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/probe", "raw_path": b"/probe", "query_string": b"", "root_path": "", "headers": [(b"host", b"127.0.0.1")], "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8000), **changes}


def run_boundary(messages: list[dict], headers: list[tuple[bytes, bytes]] | None = None, **changes: object) -> tuple:
    bodies = []

    async def endpoint(request_scope: dict, receive: object, send: object) -> None:
        bodies.append((await receive())["body"])
        await JSONResponse({"received": len(bodies[-1])})(request_scope, receive, send)

    app = AsyncMock(side_effect=endpoint)
    receive = AsyncMock(side_effect=messages)
    send = AsyncMock()
    request_scope = scope(**changes)
    if headers is not None:
        request_scope["headers"] = [(b"host", b"127.0.0.1"), *headers]
    asyncio.run(HttpSafeguardsMiddleware(app)(request_scope, receive, send))
    output = [call.args[0] for call in send.await_args_list]
    return app, receive, bodies, output


def assert_error(output: list[dict], status: int) -> dict:
    assert output[0]["status"] == status
    assert (b"cache-control", b"no-store") in output[0]["headers"]
    assert (b"x-content-type-options", b"nosniff") in output[0]["headers"]
    payload = json.loads(output[1]["body"])
    assert payload["kind"] == "error"
    assert payload["error"]["code"] in {"invalid_request", "access_denied"}
    return payload


@pytest.mark.parametrize("length", [b"", b"-1", b"+1", b" 1", b"1, 1", b"9" * 5000])
def test_invalid_body_length_is_rejected_before_body_read(length: bytes) -> None:
    app, receive, _, output = run_boundary([], [(b"content-length", length)])
    assert_error(output, 400)
    app.assert_not_awaited()
    receive.assert_not_awaited()


@pytest.mark.parametrize("headers", [
    [(b"content-length", b"1"), (b"content-length", b"1")],
    [(b"content-length", b"1"), (b"transfer-encoding", b"chunked")],
])
def test_ambiguous_request_framing_is_rejected(headers: list[tuple[bytes, bytes]]) -> None:
    app, receive, _, output = run_boundary([], headers)
    assert_error(output, 400)
    app.assert_not_awaited()
    receive.assert_not_awaited()


def test_declared_oversized_body_is_rejected_without_reading_it() -> None:
    app, receive, _, output = run_boundary([], [(b"content-length", str(MAX_REQUEST_BYTES + 1).encode())])
    assert_error(output, 413)
    app.assert_not_awaited()
    receive.assert_not_awaited()


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")], [(b"transfer-encoding", b"chunked")]])
def test_streamed_size_limit_applies_to_missing_or_false_length(headers: list[tuple[bytes, bytes]]) -> None:
    messages = [
        {"type": "http.request", "body": b"x" * MAX_REQUEST_BYTES, "more_body": True},
        {"type": "http.request", "body": b"x", "more_body": True},
        {"type": "http.request", "body": b"must not be read", "more_body": False},
    ]
    app, receive, _, output = run_boundary(messages, headers)
    assert_error(output, 413)
    assert receive.await_count == 2
    app.assert_not_awaited()


def test_exact_size_limit_is_replayed_once_to_the_application() -> None:
    data = b"x" * MAX_REQUEST_BYTES
    messages = [{"type": "http.request", "body": data[:100], "more_body": True}, {"type": "http.request", "body": data[100:], "more_body": False}]
    app, receive, bodies, output = run_boundary(messages, [(b"content-length", str(len(data)).encode())])
    assert output[0]["status"] == 200
    assert bodies == [data]
    assert receive.await_count == 2
    app.assert_awaited_once()


@pytest.mark.parametrize("declared", [b"0", b"2"])
def test_truncated_or_mismatched_body_never_reaches_application(declared: bytes) -> None:
    app, _, _, output = run_boundary([{"type": "http.request", "body": b"x"}], [(b"content-length", declared)])
    assert_error(output, 400)
    app.assert_not_awaited()


def test_slow_request_body_times_out_before_starting_an_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.http_safeguards.REQUEST_BODY_TIMEOUT_SECONDS", 0.02)

    async def stall() -> dict:
        await asyncio.Event().wait()

    app, receive, send = AsyncMock(), AsyncMock(side_effect=stall), AsyncMock()
    asyncio.run(HttpSafeguardsMiddleware(app)(scope(), receive, send))
    payload = assert_error([call.args[0] for call in send.await_args_list], 408)
    assert "No operation was started" in payload["error"]["message"]
    app.assert_not_awaited()


def test_disconnected_upload_does_not_start_an_operation_or_report_success() -> None:
    app, _, _, output = run_boundary([{"type": "http.request", "body": b"partial", "more_body": True}, {"type": "http.disconnect"}])
    app.assert_not_awaited()
    assert output == []


@pytest.mark.parametrize("headers", [
    [(b"origin", b"https://external.example")],
    [(b"origin", b"http://127.0.0.1"), (b"origin", b"http://127.0.0.1")],
    [(b"host", b"external.example")],
    [(b"origin", b"http://127.0.0.1:5173.external.example")],
])
def test_access_checks_reject_before_reading_untrusted_body(headers: list[tuple[bytes, bytes]]) -> None:
    app, receive, _, output = run_boundary([], headers)
    assert_error(output, 403)
    app.assert_not_awaited()
    receive.assert_not_awaited()


def test_remote_peer_cannot_bypass_local_access_with_forwarded_headers() -> None:
    app, receive, _, output = run_boundary([], [(b"x-forwarded-for", b"127.0.0.1"), (b"x-forwarded-host", b"localhost")], client=("203.0.113.8", 50000))
    assert_error(output, 403)
    app.assert_not_awaited()
    receive.assert_not_awaited()


def test_non_http_lifespan_events_pass_through() -> None:
    app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    request_scope = {"type": "lifespan"}
    asyncio.run(HttpSafeguardsMiddleware(app)(request_scope, receive, send))
    app.assert_awaited_once_with(request_scope, receive, send)


@pytest.fixture
def api(tmp_path: Path) -> tuple:
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(return_value=TurnPlan(action="message"))
    with TestClient(create_app(Settings(database_path=tmp_path / "http-safety.sqlite3"), provider=provider), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        yield client, provider


def test_large_json_padding_does_not_consume_quota_or_create_messages(api: tuple) -> None:
    client, provider = api
    conversation_id = client.post(BASE).json()["conversation_id"]
    url = f"{BASE}/{conversation_id}/messages"
    payload = b'{"instruction":"Save a fictional preference."}' + b" " * MAX_REQUEST_BYTES
    response = client.post(url, content=payload, headers={"Content-Type": "application/json"})
    assert response.status_code == 413 and response.json()["error"]["code"] == "invalid_request"
    assert client.get(url).json()["items"] == []
    provider.generate.assert_not_awaited()


def test_largest_supported_escaped_unicode_inputs_fit_the_http_budget(api: tuple) -> None:
    client, provider = api
    conversation_id = client.post(BASE).json()["conversation_id"]
    payload = {"instruction": "\U0001f9f5" * 4000, "examples": [str(index) + "\U0001f9f5" * 9999 for index in range(3)]}
    raw = json.dumps(payload, ensure_ascii=True).encode()
    assert 400_000 < len(raw) < MAX_REQUEST_BYTES
    response = client.post(f"{BASE}/{conversation_id}/messages", content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 200 and response.json()["kind"] == "message"
    provider.generate.assert_awaited_once()


@pytest.mark.parametrize("failure", ["unexpected", "database", "invalid_response"])
def test_server_failures_are_json_and_never_log_exception_data(api: tuple, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str) -> None:
    client, provider = api
    secret = "PRIVATE_SOURCE_AND_FAKE_KEY_CANARY"
    caplog.set_level(logging.INFO)
    if failure == "invalid_response":
        replacement = Mock(return_value={"items": [secret], "next_offset": None})
    else:
        replacement = Mock(side_effect=SQLAlchemyError(secret) if failure == "database" else RuntimeError(secret))
    monkeypatch.setattr(client.app.state.repository, "list_conversations", replacement)

    response = client.get(BASE)

    assert response.status_code == (503 if failure == "database" else 500)
    assert response.json()["kind"] == "error"
    assert response.json()["error"]["code"] == ("storage_failed" if failure == "database" else "internal_error")
    assert secret not in response.text + caplog.text
    assert response.headers["cache-control"] == "no-store"
    assert all(record.exc_info is None for record in caplog.records)
    assert client.get("/health").json() == {"status": "ok"}
    provider.generate.assert_not_awaited()


def test_default_server_host_and_openapi_describe_safeguards(api: tuple) -> None:
    client, _ = api
    assert Config("app.main:app", log_config=None).host == "127.0.0.1"
    schema = client.get("/openapi.json").json()
    responses = schema["paths"][BASE + "/{conversation_id}/messages"]["post"]["responses"]
    assert {"400", "408", "413", "500"} <= responses.keys()


@pytest.mark.parametrize("host", [b"[::1", b"localhost:invalid", b"localhost:65536", b"localhost/path", b"localhost?query", b"foreign@localhost", b"local\thost", b""])
def test_malformed_host_returns_client_error_without_starting_an_operation(host: bytes) -> None:
    app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
    asyncio.run(HttpSafeguardsMiddleware(app)(scope(headers=[(b"host", host)]), receive, send))
    assert_error([call.args[0] for call in send.await_args_list], 400)
    app.assert_not_awaited()
    receive.assert_not_awaited()
