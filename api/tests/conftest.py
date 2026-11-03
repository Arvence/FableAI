import socket
from pathlib import Path

import httpx
import httpx2
import pytest

from app import config


@pytest.fixture(autouse=True)
def isolate_local_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "FABLEAI_DATABASE_PATH"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def block_network_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_request(*args: object, **kwargs: object) -> None:
        pytest.fail("Tests must use mocked DNS and an injected client or in-memory HTTP transport.")

    monkeypatch.setattr(socket, "getaddrinfo", reject_request)
    for module in (httpx, httpx2):
        monkeypatch.setattr(module.HTTPTransport, "handle_request", reject_request)
        monkeypatch.setattr(module.AsyncHTTPTransport, "handle_async_request", reject_request)
