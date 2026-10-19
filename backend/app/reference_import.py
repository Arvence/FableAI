import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from email.message import Message

import httpx2
from pydantic import ValidationError

from app.contracts import ErrorInfo, ReferenceCollection, SourcePermission, SourceUrl, URLReference
from app.html_extraction import extract_readable_html
from app.reference_management import ReferenceImportRequest, SOURCE_URL_ADAPTER, canonical_source_url


MAX_RESPONSE_BYTES = 128 * 1_024
MAX_REDIRECTS = 2
IMPORT_TIMEOUT_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 5.0
BLOCKED_HOST_SUFFIXES = ("localhost", "local", "localdomain", "internal", "home.arpa", "onion")
BLOCKED_IPV6_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32"))
IMPORT_MESSAGES = {
    "confirmation_required": "A separate confirmed import action is required.",
    "permission_required": "Documented permission for automated access to this exact URL is required.",
    "unsafe_url": "This URL uses an unsupported host, credentials, scheme, or port.",
    "unsafe_address": "The source resolves to a blocked or non-public network address.",
    "dns_failed": "The source could not be safely resolved.",
    "redirect_rejected": "The redirect is invalid, repeated, or exceeds the redirect limit.",
    "http_rejected": "The source did not return an accessible HTML page; access restrictions will not be bypassed.",
    "unsupported_content": "Only uncompressed UTF-8 or ASCII HTML pages are supported.",
    "response_too_large": "The source exceeds the download size limit.",
    "invalid_response": "The source returned malformed or incomplete content.",
    "invalid_html": "The page has no supported readable HTML text or exceeds extraction limits.",
    "reference_limit": "The imported text would exceed the conversation reference limits.",
    "timeout": "The source import timed out.",
    "request_failed": "The source could not be safely retrieved.",
}


class ImportRejected(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(IMPORT_MESSAGES[reason])


def validate_public_address(value: str) -> str:
    if "%" in value:
        raise ImportRejected("unsafe_address")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ImportRejected("unsafe_address") from None
    if not address.is_global or address.is_multicast or address.is_reserved or str(address) == "168.63.129.16":
        raise ImportRejected("unsafe_address")
    if isinstance(address, ipaddress.IPv6Address) and (address.is_site_local or address.ipv4_mapped is not None or any(address in network for network in BLOCKED_IPV6_NETWORKS)):
        raise ImportRejected("unsafe_address")
    return str(address)


def validate_source_target(url: SourceUrl) -> httpx2.URL:
    target = httpx2.URL(str(url))
    host = target.host.lower()
    if target.scheme != "https" or target.userinfo or target.port not in {None, 443} or not host or "%" in host:
        raise ImportRejected("unsafe_url")
    if host.endswith(".") or any(host == suffix or host.endswith("." + suffix) for suffix in BLOCKED_HOST_SUFFIXES):
        raise ImportRejected("unsafe_url")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or len(host) > 253 or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in host.split(".")):
            raise ImportRejected("unsafe_url") from None
    else:
        validate_public_address(host)
    return target


async def resolve_source_host(host: str) -> list[str]:
    try:
        return [str(ipaddress.ip_address(host))]
    except ValueError:
        records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return list(dict.fromkeys(record[4][0] for record in records))


class ReferenceImporter:
    def __init__(self, permissions: Sequence[SourcePermission] = (), *, resolver: Callable[[str], Awaitable[list[str]]] = resolve_source_host, client_factory: Callable[..., httpx2.AsyncClient] = httpx2.AsyncClient) -> None:
        self._permissions = {str(canonical_source_url(permission.url)): permission.model_copy(deep=True) for permission in permissions}
        self._resolver = resolver
        self._client_factory = client_factory

    async def import_reference(self, references: ReferenceCollection, action: ReferenceImportRequest) -> ReferenceCollection:
        reference = next((item for item in references.urls if item.id == action.reference_id), None)
        if reference is None:
            raise ValueError("The requested URL reference does not exist in this conversation.")
        try:
            if not action.confirmed:
                raise ImportRejected("confirmation_required")
            async with asyncio.timeout(IMPORT_TIMEOUT_SECONDS):
                text, permission = await self._fetch(reference.url)
            updated = URLReference.model_validate({**reference.model_dump(), "import_status": "imported", "imported_text": text, "import_permission": permission, "error": None})
            try:
                return self._replace_reference(references, updated)
            except ValidationError:
                raise ImportRejected("reference_limit") from None
        except ImportRejected as error:
            reason = error.reason
        except (TimeoutError, httpx2.TimeoutException):
            reason = "timeout"
        except socket.gaierror:
            reason = "dns_failed"
        except (httpx2.HTTPError, OSError):
            reason = "request_failed"
        except (ValueError, TypeError):
            reason = "invalid_response"

        error = ErrorInfo(code="reference_import_failed", message=IMPORT_MESSAGES[reason] + " Paste excerpts you are permitted to use instead.")
        failed = URLReference.model_validate({**reference.model_dump(), "import_status": "failed", "imported_text": None, "import_permission": None, "error": error})
        return self._replace_reference(references, failed)

    @staticmethod
    def _replace_reference(references: ReferenceCollection, updated: URLReference) -> ReferenceCollection:
        return ReferenceCollection(examples=references.examples, headlines=references.headlines, urls=[updated if item.id == updated.id else item for item in references.urls])

    async def _fetch(self, source: SourceUrl) -> tuple[str, SourcePermission]:
        url = canonical_source_url(source)
        visited = set()
        for hop in range(MAX_REDIRECTS + 1):
            if str(url) in visited:
                raise ImportRejected("redirect_rejected")
            visited.add(str(url))
            target = validate_source_target(url)
            permission = self._permissions.get(str(url))
            if permission is None or not permission.automated_access_allowed:
                raise ImportRejected("permission_required")
            addresses = await self._resolver(target.host)
            if not addresses:
                raise ImportRejected("dns_failed")
            validated = [validate_public_address(address) for address in addresses]
            pinned_url = target.copy_with(host=validated[0])
            headers = {"Host": target.netloc.decode("ascii"), "Accept": "text/html", "Accept-Encoding": "identity", "User-Agent": "FableAI-ReferenceImport/1.0"}
            async with self._client_factory(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", pinned_url, headers=headers, extensions={"sni_hostname": target.host}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location or hop == MAX_REDIRECTS or "\\" in location or any(ord(char) < 32 or ord(char) == 127 for char in location):
                            raise ImportRejected("redirect_rejected")
                        try:
                            url = canonical_source_url(SOURCE_URL_ADAPTER.validate_python(str(target.join(location))))
                        except (ValueError, httpx2.InvalidURL):
                            raise ImportRejected("redirect_rejected") from None
                        continue
                    if response.status_code != 200:
                        raise ImportRejected("http_rejected")
                    html = await self._read_html(response)
            try:
                return extract_readable_html(html), permission.model_copy(deep=True)
            except ValueError:
                raise ImportRejected("invalid_html") from None
        raise ImportRejected("redirect_rejected")

    @staticmethod
    async def _read_html(response: httpx2.Response) -> str:
        content_type = Message()
        content_type["content-type"] = response.headers.get("content-type", "application/octet-stream")
        charset = content_type.get_content_charset() or "utf-8"
        if content_type.get_content_type() != "text/html" or charset not in {"utf-8", "utf8", "us-ascii", "ascii"} or response.headers.get("content-encoding", "identity").lower() != "identity":
            raise ImportRejected("unsupported_content")
        length = response.headers.get("content-length")
        if length is not None and (not length.isascii() or not length.isdecimal()):
            raise ImportRejected("invalid_response")
        declared_size = int(length) if length is not None else None
        if declared_size is not None and declared_size > MAX_RESPONSE_BYTES:
            raise ImportRejected("response_too_large")
        body = bytearray()
        async for chunk in response.aiter_raw():
            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                raise ImportRejected("response_too_large")
            body.extend(chunk)
        if declared_size is not None and len(body) != declared_size:
            raise ImportRejected("invalid_response")
        return body.decode("utf-8-sig" if charset in {"utf-8", "utf8"} else "ascii", errors="strict")
