import httpx
import httpx2
import pytest


@pytest.fixture(autouse=True)
def block_network_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_request(*args: object, **kwargs: object) -> None:
        pytest.fail("Tests must use an injected client or an in-memory HTTP transport.")

    for module in (httpx, httpx2):
        monkeypatch.setattr(module.HTTPTransport, "handle_request", reject_request)
        monkeypatch.setattr(module.AsyncHTTPTransport, "handle_async_request", reject_request)
