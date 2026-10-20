from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.conversation_contracts import TurnPlan
from app.gemini_provider import GeminiProvider
from app.main import create_app
from app.reference_import import ReferenceImporter


BASE = "/api/v1/conversations"
SOURCE = "https://example.com/source"


class HtmlStream(httpx2.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"<html><body><article><h1>Imaginary workshop</h1><p>A self-authored synthetic reference for an invented exhibit.</p></article></body></html>"


@pytest.fixture
def imports(tmp_path: Path) -> tuple:
    requests = []
    resolver = AsyncMock(return_value=["93.184.216.34"])
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(return_value=TurnPlan(action="message"))

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/html"}, stream=HtmlStream())

    def client_factory(**kwargs: object) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handle), **kwargs)

    def importer_factory(permissions: list) -> ReferenceImporter:
        return ReferenceImporter(permissions, resolver=resolver, client_factory=client_factory)

    settings = Settings(database_path=tmp_path / "imports.sqlite3")
    with TestClient(create_app(settings, provider=provider, importer_factory=importer_factory), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        conversation_id = client.post(BASE).json()["conversation_id"]
        response = client.post(f"{BASE}/{conversation_id}/messages", json={"instruction": "Save this reference URL.", "source_urls": [SOURCE]})
        assert response.status_code == 200
        reference_id = client.get(f"{BASE}/{conversation_id}").json()["references"]["urls"][0]["id"]
        yield client, conversation_id, reference_id, requests, resolver, provider, settings, importer_factory


def permission(**changes: object) -> dict:
    return {"url": SOURCE, "basis": "owned", "evidence": "Self-authored synthetic test fixture.", "automated_access_allowed": True, "model_use_allowed": True, "non_confidential": True, **changes}


def test_explicit_import_persists_permission_text_and_state_across_restart(imports: tuple) -> None:
    client, conversation_id, reference_id, requests, resolver, provider, settings, factory = imports
    url = f"{BASE}/{conversation_id}/references/import"
    key = str(uuid4())
    body = {"reference_id": reference_id, "confirmed": True, "permissions": [permission()]}
    first = client.post(url, json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 200, first.text
    assert first.json()["kind"] == "reference_import"
    reference = first.json()["references"]["urls"][0]
    assert reference["import_status"] == "imported"
    assert reference["import_permission"] == permission()
    assert "self-authored" in reference["imported_text"]
    assert str(requests[0].url) == "https://93.184.216.34/source"
    assert requests[0].headers["host"] == "example.com"
    assert requests[0].extensions["sni_hostname"] == "example.com"
    assert client.post(url, json=body, headers={"Idempotency-Key": key}).json() == first.json()
    assert len(requests) == 1
    resolver.assert_awaited_once()
    provider.generate.assert_awaited_once()
    assert len(client.get(f"{BASE}/{conversation_id}/messages").json()["items"]) == 2
    with TestClient(create_app(settings, provider=provider, importer_factory=factory), base_url="http://127.0.0.1", client=("127.0.0.1", 50001)) as reopened:
        assert reopened.get(f"{BASE}/{conversation_id}").json()["references"]["urls"][0] == reference
        assert reopened.post(url, json=body, headers={"Idempotency-Key": key}).json() == first.json()
        assert len(requests) == 1


@pytest.mark.parametrize(("confirmed", "permissions"), [
    (False, [permission()]),
    (True, []),
    (True, [permission(automated_access_allowed=False)]),
    (True, [permission(url="https://example.com/another")]),
])
def test_unconfirmed_or_unpermitted_imports_fail_without_network_and_persist_failure(imports: tuple, confirmed: bool, permissions: list) -> None:
    client, conversation_id, reference_id, requests, resolver, provider, _, _ = imports
    body = {"reference_id": reference_id, "confirmed": confirmed, "permissions": permissions}
    response = client.post(f"{BASE}/{conversation_id}/references/import", json=body)
    assert response.status_code == 422 and response.json()["error"]["code"] == "reference_import_failed"
    reference = client.get(f"{BASE}/{conversation_id}").json()["references"]["urls"][0]
    assert reference["import_status"] == "failed"
    assert reference["imported_text"] is reference["import_permission"] is None
    assert requests == []
    resolver.assert_not_awaited()
    provider.generate.assert_awaited_once()


def test_private_resolution_is_rejected_without_http_request(imports: tuple) -> None:
    client, conversation_id, reference_id, requests, resolver, _, _, _ = imports
    resolver.return_value = ["127.0.0.1"]
    response = client.post(f"{BASE}/{conversation_id}/references/import", json={"reference_id": reference_id, "confirmed": True, "permissions": [permission()]})
    assert response.status_code == 422 and "non-public" in response.json()["error"]["message"]
    assert requests == []


def test_reference_id_from_another_conversation_does_not_fetch(imports: tuple) -> None:
    client, _, reference_id, requests, resolver, _, _, _ = imports
    another = client.post(BASE).json()["conversation_id"]
    response = client.post(f"{BASE}/{another}/references/import", json={"reference_id": reference_id, "confirmed": True, "permissions": [permission()]})
    assert response.status_code == 404 and response.json()["error"]["code"] == "reference_not_found"
    assert requests == []
    resolver.assert_not_awaited()


def test_permission_to_fetch_does_not_imply_permission_to_send_source_to_model(imports: tuple) -> None:
    client, conversation_id, reference_id, requests, _, provider, _, _ = imports
    imported = client.post(f"{BASE}/{conversation_id}/references/import", json={"reference_id": reference_id, "confirmed": True, "permissions": [permission(model_use_allowed=False, non_confidential=False)]})
    assert imported.status_code == 200
    provider.generate.return_value = TurnPlan(action="message")
    assert client.post(f"{BASE}/{conversation_id}/messages", json={"instruction": "Keep the saved reference."}).status_code == 200
    prompt = provider.generate.call_args.args[0]
    assert "self-authored synthetic reference for an invented exhibit" not in prompt
    assert "not_provided_for_model_use" in prompt
    assert len(requests) == 1


def test_message_import_flag_only_records_link_metadata(imports: tuple) -> None:
    client, _, _, requests, resolver, provider, _, _ = imports
    conversation_id = client.post(BASE).json()["conversation_id"]
    calls = provider.generate.await_count
    response = client.post(f"{BASE}/{conversation_id}/messages", json={"instruction": "Import this reference.", "source_urls": [SOURCE], "import_urls": True})
    assert response.status_code == 200 and response.json()["kind"] == "clarification"
    assert client.get(f"{BASE}/{conversation_id}").json()["references"]["urls"][0]["import_status"] == "not_requested"
    assert requests == [] and provider.generate.await_count == calls
    resolver.assert_not_awaited()
