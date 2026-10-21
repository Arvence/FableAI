import asyncio
import json
import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

import httpx
import httpx2
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.contracts import DraftBatch, SourcePermission
from app.conversation_contracts import GenerationPreferences, TurnPlan
from app.gemini_provider import GeminiProvider, GeminiProviderError, ProviderFailure
from app.main import create_app
from app.reference_import import ReferenceImporter
from app.revision_generation import RevisionBatch


CONTENTS = [
    {"title": "[FICTIONAL] Kite Workshop Adds Floating Mosaics", "body": "In the invented harbor of Tessara, painted kites carry a shifting mosaic over a paper pier. Visitors arrange bright tiles above the pretend tide.", "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"},
    {"title": "[FICTIONAL] Clock Orchard Hosts a Quiet Design Trail", "body": "Across an imaginary valley, wooden trees hold clocks with colorful leaves. A local craft group invites visitors to explore winding paths through the make-believe exhibit.", "category": "announcement", "tags": ["fiction"], "language": "en", "is_synthetic": True, "status": "draft"},
]


async def generate(prompt: str, schema: type) -> object:
    if schema is TurnPlan:
        instruction = json.loads(prompt.split("DATA_JSON:\n", 1)[1])["conversation"]["current_instruction"].lower()
        if "quota" in instruction:
            raise GeminiProviderError(ProviderFailure.RATE_LIMITED)
        if "invalid response" in instruction:
            raise GeminiProviderError(ProviderFailure.INVALID_RESPONSE)
        if "slow" in instruction:
            await asyncio.sleep(1)
        if "shorter" in instruction:
            return TurnPlan(action="revise", length_change="shorter")
        if "generate" in instruction:
            return TurnPlan(action="generate")
        if "remember" in instruction:
            return TurnPlan(action="message", preferences=GenerationPreferences(topic="An imaginary harbor festival", count=2, tone="neutral", category="announcement", language="en", min_body_chars=60, max_body_chars=300))
        return TurnPlan(action="message")
    if schema is DraftBatch:
        return DraftBatch.model_validate({"entries": CONTENTS})
    if schema is RevisionBatch:
        data, _ = json.JSONDecoder().raw_decode(prompt.split("REVISION_JSON:\n", 1)[1])
        return RevisionBatch.model_validate({"entries": [{"entry_id": target["entry_id"], **target["original"], "body": "In an imaginary orchard, painted clocks hang from wooden branches. Visitors follow a short design trail."} for target in data["targets"]]})
    raise AssertionError("Unexpected response schema.")


class HtmlStream(httpx2.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"<html><body><article><h1>Invented exhibit</h1><p>An original synthetic sample describing a pretend lantern display.</p></article></body></html>"


def blocked_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Browser tests must never use a real outbound HTTP transport.")


def run() -> None:
    provider = Mock(spec=GeminiProvider)
    provider.generate = AsyncMock(side_effect=generate)
    imported_requests = []

    async def resolver(host: str) -> list[str]:
        return ["93.184.216.34"]

    def handle(request: httpx2.Request) -> httpx2.Response:
        imported_requests.append(str(request.url))
        return httpx2.Response(200, headers={"content-type": "text/html"}, stream=HtmlStream())

    def client_factory(**kwargs: object) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(handle), **kwargs)

    def importer_factory(permissions: Sequence[SourcePermission]) -> ReferenceImporter:
        return ReferenceImporter(permissions, resolver=resolver, client_factory=client_factory)

    with TemporaryDirectory(prefix="fableai-browser-") as directory:
        application = create_app(Settings(database_path=Path(directory) / "test.sqlite3"), provider=provider, importer_factory=importer_factory)

        @application.get("/api/v1/test-state")
        def test_state() -> dict:
            return {"provider_calls": provider.generate.await_count, "import_requests": len(imported_requests)}

        with patch.object(httpx.HTTPTransport, "handle_request", blocked_network), patch.object(httpx.AsyncHTTPTransport, "handle_async_request", blocked_network), patch.object(httpx2.HTTPTransport, "handle_request", blocked_network), patch.object(httpx2.AsyncHTTPTransport, "handle_async_request", blocked_network):
            uvicorn.run(application, host="127.0.0.1", port=8000, access_log=False)


if __name__ == "__main__":
    run()
