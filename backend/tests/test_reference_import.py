import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx2
import pytest

from app.contracts import MAX_REFERENCE_CHARS, ReferenceCollection, ReferenceText, SourcePermission, URLReference
from app.reference_import import MAX_RESPONSE_BYTES, ReferenceImporter, resolve_source_host
from app.reference_management import ReferenceImportRequest, prepare_reference_data


SOURCE_URL = "https://example.com/source"
PUBLIC_IP = "93.184.216.34"
HTML = b"<!DOCTYPE html><html><body><article><h1>Fictional garden</h1><p>A controlled, self-authored fixture.</p></article></body></html>"


class FixtureStream(httpx2.AsyncByteStream):
    def __init__(self, chunks: list[bytes], *, error: BaseException | None = None, stall: bool = False) -> None:
        self.chunks = chunks
        self.error = error
        self.stall = stall
        self.closed = False
        self.cancelled = False
        self.read_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            if self.stall:
                await asyncio.Event().wait()
            for chunk in self.chunks:
                self.read_count += 1
                yield chunk
            if self.error:
                raise self.error
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def aclose(self) -> None:
        self.closed = True


def html_response(body: bytes = HTML, *, status: int = 200, headers: dict | None = None, stream: FixtureStream | None = None) -> httpx2.Response:
    return httpx2.Response(status, headers={"content-type": "text/html; charset=utf-8", **(headers or {})}, stream=stream or FixtureStream([body]))


def source_permission(url: str = SOURCE_URL, **changes: object) -> SourcePermission:
    return SourcePermission.model_validate({"url": url, "basis": "owned", "evidence": "Self-authored synthetic test fixture; automated retrieval is expressly permitted.", "automated_access_allowed": True, **changes})


def source_references(url: str = SOURCE_URL) -> ReferenceCollection:
    return ReferenceCollection(urls=[URLReference(id=uuid4(), url=url, label="Synthetic fixture")])


def import_once(importer: ReferenceImporter, references: ReferenceCollection, *, confirmed: bool = True) -> ReferenceCollection:
    return asyncio.run(importer.import_reference(references, ReferenceImportRequest(reference_id=references.urls[0].id, confirmed=confirmed)))


def assert_failed(result: ReferenceCollection, message: str) -> None:
    reference = result.urls[0]
    assert reference.import_status == "failed"
    assert reference.imported_text is reference.import_permission is None
    assert reference.error.code == "reference_import_failed"
    assert message.lower() in reference.error.message.lower()
    assert "Paste excerpts you are permitted to use instead." in reference.error.message


@pytest.fixture
def importer_factory() -> Callable:
    def build(handler: Callable | None = None, *, permissions: list[SourcePermission] | None = None, addresses: list[str] | None = None) -> tuple[ReferenceImporter, list[httpx2.Request], AsyncMock, Mock]:
        requests = []

        def handle(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return handler(request) if handler else html_response()

        def create_client(**kwargs: object) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(transport=httpx2.MockTransport(handle), **kwargs)

        factory = Mock(side_effect=create_client)
        resolver = AsyncMock(return_value=addresses if addresses is not None else [PUBLIC_IP])
        importer = ReferenceImporter([source_permission()] if permissions is None else permissions, resolver=resolver, client_factory=factory)
        return importer, requests, resolver, factory

    return build


def test_explicit_allowed_import_pins_ip_preserves_tls_host_and_closes_response(importer_factory: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = FixtureStream([HTML[:40], HTML[40:]])
    importer, requests, resolver, factory = importer_factory(lambda request: html_response(stream=stream, headers={"content-length": str(len(HTML))}))
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1234")
    current = source_references()
    before = current.model_dump_json()

    result = import_once(importer, current)

    reference = result.urls[0]
    assert reference.id == current.urls[0].id
    assert reference.import_status == "imported"
    assert reference.imported_text == "Fictional garden A controlled, self-authored fixture."
    assert reference.label == "Synthetic fixture"
    assert reference.import_permission == source_permission()
    assert reference.error is None
    assert current.model_dump_json() == before
    resolver.assert_awaited_once_with("example.com")
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == f"https://{PUBLIC_IP}/source"
    assert request.method == "GET"
    assert request.headers["host"] == "example.com"
    assert request.headers["accept-encoding"] == "identity"
    assert request.headers["accept"] == "text/html"
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert request.extensions["sni_hostname"] == "example.com"
    assert request.extensions["timeout"] == {"connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0}
    assert factory.call_args.kwargs == {"timeout": 5.0, "follow_redirects": False, "trust_env": False}
    assert stream.closed
    assert ReferenceCollection.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(("confirmed", "permissions", "message"), [
    (False, [source_permission()], "confirmed import"),
    (True, [], "Documented permission"),
    (True, [source_permission(automated_access_allowed=False)], "Documented permission"),
    (True, [source_permission("https://example.com/different")], "exact URL"),
    (True, [source_permission("https://example.com/source?other=1")], "exact URL"),
])
def test_unconfirmed_or_unapproved_sources_do_not_resolve_or_fetch(importer_factory: Callable, confirmed: bool, permissions: list[SourcePermission], message: str) -> None:
    importer, requests, resolver, factory = importer_factory(permissions=permissions)

    result = import_once(importer, source_references(), confirmed=confirmed)

    assert_failed(result, message)
    assert requests == []
    resolver.assert_not_awaited()
    factory.assert_not_called()


def test_unknown_reference_does_not_fetch(importer_factory: Callable) -> None:
    importer, requests, resolver, _ = importer_factory()
    with pytest.raises(ValueError, match="does not exist"):
        asyncio.run(importer.import_reference(source_references(), ReferenceImportRequest(reference_id=uuid4(), confirmed=True)))
    assert requests == []
    resolver.assert_not_awaited()


@pytest.mark.parametrize("url", [
    "https://localhost/", "https://sub.localhost/", "https://metadata.google.internal/", "https://router.local/", "https://printer/",
    "https://example.com./source", "https://example.com:8443/source", "https://user:password@example.com/source",
    "https://127.0.0.1/", "https://127.1/", "https://2130706433/", "https://0x7f000001/", "https://10.0.0.1/",
    "https://169.254.169.254/", "https://168.63.129.16/", "https://100.100.100.200/", "https://[::1]/", "https://[fd00:ec2::254]/",
])
def test_blocks_unsafe_targets_before_dns_even_with_permission(importer_factory: Callable, url: str) -> None:
    importer, requests, resolver, factory = importer_factory(permissions=[source_permission(url)])

    result = import_once(importer, source_references(url))

    assert_failed(result, "unsupported" if "example.com" in url or "localhost" in url or "internal" in url or "router.local" in url or "printer" in url else "blocked")
    assert requests == []
    resolver.assert_not_awaited()
    factory.assert_not_called()


@pytest.mark.parametrize("address", [
    "0.0.0.0", "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "168.63.129.16",
    "224.0.0.1", "240.0.0.1", "192.0.2.1", "198.18.0.1", "::", "::1", "fd00::1", "fe80::1", "fec0::1", "ff02::1",
    "2001:db8::1", "::ffff:127.0.0.1", "::ffff:8.8.8.8", "64:ff9b::7f00:1", "64:ff9b:1::7f00:1", "2002:7f00:1::", "2001::1234",
    "2606:4700:4700::1111%scope", "invalid-address",
])
def test_blocks_non_public_dns_answers_and_mixed_records(importer_factory: Callable, address: str) -> None:
    importer, requests, resolver, factory = importer_factory(addresses=[PUBLIC_IP, address])

    result = import_once(importer, source_references())

    assert_failed(result, "blocked")
    assert requests == []
    resolver.assert_awaited_once()
    factory.assert_not_called()


def test_public_ipv6_is_pinned_with_original_hostname(importer_factory: Callable) -> None:
    importer, requests, _, _ = importer_factory(addresses=["2606:4700:4700::1111"])

    result = import_once(importer, source_references())

    assert result.urls[0].import_status == "imported"
    assert str(requests[0].url) == "https://[2606:4700:4700::1111]/source"
    assert requests[0].extensions["sni_hostname"] == "example.com"


def test_resolver_returns_all_unique_addresses_and_skips_lookup_for_ip() -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        lookup = AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443)), (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))])
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(loop, "getaddrinfo", lookup)
            assert await resolve_source_host("example.com") == [PUBLIC_IP, "::1"]
            assert await resolve_source_host(PUBLIC_IP) == [PUBLIC_IP]
            lookup.assert_awaited_once_with("example.com", 443, type=socket.SOCK_STREAM)

    asyncio.run(run())


@pytest.mark.parametrize("addresses", [[], ["invalid-address"]])
def test_empty_or_malformed_dns_fails_closed(importer_factory: Callable, addresses: list[str]) -> None:
    importer, requests, _, _ = importer_factory(addresses=addresses)
    result = import_once(importer, source_references())
    assert_failed(result, "resolved" if not addresses else "blocked")
    assert requests == []


def test_dns_error_is_safe_and_does_not_fetch(importer_factory: Callable) -> None:
    importer, requests, resolver, _ = importer_factory()
    resolver.side_effect = socket.gaierror("private internal DNS diagnostic")
    result = import_once(importer, source_references())
    assert_failed(result, "resolved")
    assert "private internal" not in result.model_dump_json()
    assert requests == []


def test_approved_redirect_is_revalidated_and_does_not_forward_cookies(importer_factory: Callable) -> None:
    final_url = "https://other.example.com/final"
    first = html_response(status=302, headers={"location": final_url, "set-cookie": "private=session; Secure"})
    second = html_response()
    responses = iter([first, second])
    permissions = [source_permission(), source_permission(final_url, model_use_allowed=True, non_confidential=True)]
    importer, requests, resolver, factory = importer_factory(lambda request: next(responses), permissions=permissions)

    result = import_once(importer, source_references())

    assert result.urls[0].import_status == "imported"
    assert str(result.urls[0].url) == SOURCE_URL
    assert str(result.urls[0].import_permission.url) == final_url
    assert json.loads(prepare_reference_data(result).content)["links"][0]["source_text"] is not None
    assert resolver.await_args_list[0].args == ("example.com",)
    assert resolver.await_args_list[1].args == ("other.example.com",)
    assert len(requests) == factory.call_count == 2
    assert "cookie" not in requests[1].headers
    assert requests[1].headers["host"] == "other.example.com"
    assert requests[1].extensions["sni_hostname"] == "other.example.com"
    assert first.is_closed and second.is_closed


@pytest.mark.parametrize("location", ["http://example.com/final", "file:///etc/passwd", "https://127.0.0.1/", "https://metadata.google.internal/", "https://unapproved.example.com/", "/unapproved", "https://user:pass@example.com/final", "//localhost/", "/source", "https://example.com\\@127.0.0.1/", "\r\nInjected: value"])
def test_unsafe_unapproved_or_repeated_redirect_is_not_followed(importer_factory: Callable, location: str) -> None:
    importer, requests, resolver, _ = importer_factory(lambda request: html_response(status=302, headers={"location": location}))

    result = import_once(importer, source_references())

    assert_failed(result, "")
    assert len(requests) == 1
    resolver.assert_awaited_once()


def test_redirect_dns_rebinding_is_rejected_before_second_request(importer_factory: Callable) -> None:
    final_url = "https://example.com/final"
    importer, requests, resolver, _ = importer_factory(lambda request: html_response(status=302, headers={"location": "/final"}), permissions=[source_permission(), source_permission(final_url)])
    resolver.side_effect = [[PUBLIC_IP], ["127.0.0.1"]]

    result = import_once(importer, source_references())

    assert_failed(result, "blocked")
    assert len(requests) == 1
    assert resolver.await_count == 2


def test_redirect_count_is_bounded(importer_factory: Callable) -> None:
    locations = iter(["/one", "/two", "/three"])
    importer, requests, resolver, _ = importer_factory(lambda request: html_response(status=302, headers={"location": next(locations)}), permissions=[source_permission(), *(source_permission(f"https://example.com/{path}") for path in ["one", "two", "three"])])

    result = import_once(importer, source_references())

    assert_failed(result, "redirect limit")
    assert len(requests) == resolver.await_count == 3


@pytest.mark.parametrize("status", [204, 206, 300, 304, 400, 401, 403, 404, 429, 500, 503])
def test_http_failures_do_not_retry_or_read_access_restriction_bodies(importer_factory: Callable, status: int) -> None:
    stream = FixtureStream([b"Untrusted error or login page"])
    importer, requests, _, _ = importer_factory(lambda request: html_response(status=status, stream=stream))
    result = import_once(importer, source_references())
    assert_failed(result, "accessible HTML")
    assert len(requests) == 1
    assert stream.read_count == 0
    assert stream.closed


@pytest.mark.parametrize("headers", [
    {"content-type": "text/plain"}, {"content-type": "application/pdf"}, {"content-type": "application/json"},
    {"content-type": "application/xhtml+xml"}, {"content-type": "text/html; charset=iso-8859-1"},
    {"content-encoding": "gzip"}, {"content-encoding": "br"}, {"content-type": "text/html, application/json"},
])
def test_unsupported_content_fails_before_reading_body(importer_factory: Callable, headers: dict) -> None:
    stream = FixtureStream([HTML])
    importer, requests, _, _ = importer_factory(lambda request: html_response(headers=headers, stream=stream))
    result = import_once(importer, source_references())
    assert_failed(result, "Only uncompressed")
    assert len(requests) == 1
    assert stream.read_count == 0 and stream.closed


def test_missing_content_type_is_rejected(importer_factory: Callable) -> None:
    importer, _, _, _ = importer_factory(lambda request: httpx2.Response(200, stream=FixtureStream([HTML])))
    assert_failed(import_once(importer, source_references()), "Only uncompressed")


@pytest.mark.parametrize("length", [str(MAX_RESPONSE_BYTES + 1), "-1", "+100", " 100", "not-a-number", "3, 5"])
def test_invalid_or_oversized_content_length_prevents_body_read(importer_factory: Callable, length: str) -> None:
    stream = FixtureStream([HTML])
    importer, _, _, _ = importer_factory(lambda request: html_response(headers={"content-length": length}, stream=stream))
    assert_failed(import_once(importer, source_references()), "")
    assert stream.read_count == 0 and stream.closed


def test_streamed_size_cap_stops_reading_without_content_length(importer_factory: Callable) -> None:
    stream = FixtureStream([b"x" * MAX_RESPONSE_BYTES, b"x", b"never read"])
    importer, requests, _, _ = importer_factory(lambda request: html_response(stream=stream))
    assert_failed(import_once(importer, source_references()), "size limit")
    assert len(requests) == 1
    assert stream.read_count == 2 and stream.closed


def test_accepts_exact_download_limit_with_bounded_readable_text(importer_factory: Callable) -> None:
    prefix = b"<body><p>Permitted synthetic text.</p><!--"
    suffix = b"--></body>"
    body = prefix + b"x" * (MAX_RESPONSE_BYTES - len(prefix) - len(suffix)) + suffix
    importer, _, _, _ = importer_factory(lambda request: html_response(body, headers={"content-length": str(MAX_RESPONSE_BYTES)}))

    result = import_once(importer, source_references())

    assert result.urls[0].import_status == "imported"
    assert result.urls[0].imported_text == "Permitted synthetic text."


@pytest.mark.parametrize("body", [b"<body>invalid\xff</body>", b"<body>bad\x00text</body>", b"<body>unfinished", b"<body><script>no readable text</script></body>", b"<body>" + b"x" * (MAX_REFERENCE_CHARS + 1) + b"</body>"])
def test_malformed_or_oversized_extracted_content_fails_closed(importer_factory: Callable, body: bytes) -> None:
    importer, requests, _, _ = importer_factory(lambda request: html_response(body))
    assert_failed(import_once(importer, source_references()), "")
    assert len(requests) == 1


def test_truncated_content_is_not_accepted(importer_factory: Callable) -> None:
    importer, _, _, _ = importer_factory(lambda request: html_response(headers={"content-length": str(len(HTML) + 1)}))
    assert_failed(import_once(importer, source_references()), "incomplete")


@pytest.mark.parametrize(("exception", "message"), [(httpx2.ReadTimeout("secret diagnostic"), "timed out"), (httpx2.ConnectError("secret diagnostic"), "safely retrieved"), (httpx2.RemoteProtocolError("secret diagnostic"), "safely retrieved")])
def test_transport_errors_are_safe_and_not_retried(importer_factory: Callable, exception: Exception, message: str) -> None:
    def fail(request: httpx2.Request) -> httpx2.Response:
        raise exception

    importer, requests, _, _ = importer_factory(fail)
    result = import_once(importer, source_references())
    assert_failed(result, message)
    assert "secret diagnostic" not in result.model_dump_json()
    assert len(requests) == 1


def test_total_import_deadline_covers_slow_body(importer_factory: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.reference_import.IMPORT_TIMEOUT_SECONDS", 0.02)
    stream = FixtureStream([], stall=True)
    importer, requests, _, _ = importer_factory(lambda request: html_response(stream=stream))
    assert_failed(import_once(importer, source_references()), "timed out")
    assert stream.cancelled and stream.closed
    assert len(requests) == 1


def test_total_import_deadline_covers_dns(importer_factory: Callable, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.reference_import.IMPORT_TIMEOUT_SECONDS", 0.02)
    importer, requests, resolver, factory = importer_factory()

    async def stall(host: str) -> list[str]:
        await asyncio.Event().wait()

    resolver.side_effect = stall
    assert_failed(import_once(importer, source_references()), "timed out")
    assert requests == []
    factory.assert_not_called()


def test_stream_failure_closes_response_and_returns_safe_error(importer_factory: Callable) -> None:
    stream = FixtureStream([b"<body>"], error=httpx2.ReadError("private diagnostic"))
    importer, requests, _, _ = importer_factory(lambda request: html_response(stream=stream))

    result = import_once(importer, source_references())

    assert_failed(result, "safely retrieved")
    assert "private diagnostic" not in result.model_dump_json()
    assert stream.closed
    assert len(requests) == 1


def test_caller_cancellation_propagates_and_closes_response(importer_factory: Callable) -> None:
    stream = FixtureStream([b"<body>"], error=asyncio.CancelledError())
    importer, requests, _, _ = importer_factory(lambda request: html_response(stream=stream))

    with pytest.raises(asyncio.CancelledError):
        import_once(importer, source_references())

    assert stream.cancelled and stream.closed
    assert len(requests) == 1


def test_relative_redirect_uses_original_host_and_final_source_permission(importer_factory: Callable) -> None:
    final_url = "https://example.com/final"
    responses = iter([html_response(status=302, headers={"location": "/final#section"}), html_response()])
    importer, requests, resolver, _ = importer_factory(lambda request: next(responses), permissions=[source_permission(model_use_allowed=True, non_confidential=True), source_permission(final_url)])

    result = import_once(importer, source_references())

    assert result.urls[0].import_status == "imported"
    assert str(result.urls[0].import_permission.url) == final_url
    assert str(requests[1].url) == f"https://{PUBLIC_IP}/final"
    assert resolver.await_count == 2
    assert json.loads(prepare_reference_data(result).content)["links"][0]["source_text"] is None


def test_combined_reference_budget_is_checked_before_accepting_import(importer_factory: Callable) -> None:
    importer, _, _, _ = importer_factory()
    reference = source_references().urls[0]
    remaining = 30_000 - len(str(reference.url)) - len(reference.label)
    current = ReferenceCollection(examples=[ReferenceText(id=uuid4(), text="a" * 10_000), ReferenceText(id=uuid4(), text="b" * 10_000), ReferenceText(id=uuid4(), text="c" * (remaining - 20_000))], urls=[reference])
    before = current.model_dump_json()

    result = import_once(importer, current)

    assert_failed(result, "conversation reference limits")
    assert current.model_dump_json() == before


def test_failed_reimport_clears_old_source_text_and_permission(importer_factory: Callable) -> None:
    reference = URLReference(id=uuid4(), url=SOURCE_URL, import_status="imported", imported_text="Old local source text.", import_permission=source_permission())
    importer, requests, resolver, _ = importer_factory(permissions=[])
    result = import_once(importer, ReferenceCollection(urls=[reference]))
    assert_failed(result, "permission")
    assert requests == []
    resolver.assert_not_awaited()
