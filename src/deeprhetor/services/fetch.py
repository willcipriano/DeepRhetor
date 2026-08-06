"""Secure public HTTP(S) document fetcher with SSRF protections."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import urlparse

import httpx

from deeprhetor.domain.sources import FetchRequest, FetchResult

DEFAULT_USER_AGENT = "DeepRhetor/0.1 (+https://github.com/willcipriano/DeepRhetor; research archive)"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_BYTES = 52_428_800  # 50 MiB
DEFAULT_MAX_REDIRECTS = 5

BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)


class FetchError(Exception):
    """Raised when a fetch is refused or fails safety checks."""


class SSRFBlockedError(FetchError):
    """Raised when a URL targets a disallowed host or network."""


class SecureHttpFetcher:
    """Fetch public HTTP(S) resources only; block private/loopback targets."""

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> SecureHttpFetcher:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout_seconds,
            )
            self._owns_client = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, request: FetchRequest) -> FetchResult:
        max_bytes = request.max_bytes if request.max_bytes is not None else self.max_bytes
        timeout = (
            request.timeout_seconds
            if request.timeout_seconds is not None
            else self.timeout_seconds
        )
        validate_public_url(request.url)

        own_client = False
        client = self._client
        if client is None:
            client = httpx.AsyncClient(
                follow_redirects=False,
                headers={"User-Agent": self.user_agent},
                timeout=timeout,
            )
            own_client = True

        try:
            current_url = request.url
            response: httpx.Response | None = None
            redirects = 0
            while True:
                validate_public_url(current_url)
                response = await client.request("GET", current_url, timeout=timeout)
                if response.is_redirect and request.follow_redirects:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError("redirect without Location header")
                    next_url = str(response.url.join(location))
                    validate_public_url(next_url)
                    redirects += 1
                    if redirects > self.max_redirects:
                        raise FetchError(f"too many redirects (>{self.max_redirects})")
                    current_url = next_url
                    continue
                break

            assert response is not None
            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code} for {current_url}")

            content_type = response.headers.get("content-type", "application/octet-stream")
            media_type = content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"
            if request.allowed_media_types is not None:
                allowed = {m.lower() for m in request.allowed_media_types}
                if media_type not in allowed and "*/*" not in allowed:
                    raise FetchError(f"disallowed media type: {media_type}")

            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        raise FetchError(
                            f"content-length {content_length} exceeds max_bytes {max_bytes}"
                        )
                except ValueError:
                    pass

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError(f"response exceeds max_bytes {max_bytes}")
                chunks.append(chunk)
            content = b"".join(chunks)
            digest = hashlib.sha256(content).hexdigest()
            headers = {k.lower(): v for k, v in response.headers.items()}
            return FetchResult(
                original_url=request.url,
                final_url=str(response.url),
                media_type=media_type,
                content=content,
                headers=headers,
                byte_size=len(content),
                sha256=digest,
                status_code=response.status_code,
            )
        finally:
            if own_client:
                await client.aclose()


def validate_public_url(url: str) -> None:
    """Reject non-HTTP(S) schemes and private/loopback/link-local destinations."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise SSRFBlockedError(f"unsupported URL scheme: {scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise SSRFBlockedError("URL missing hostname")
    if host.lower() in BLOCKED_HOSTNAMES:
        raise SSRFBlockedError(f"blocked hostname: {host}")
    if host.lower().endswith(".local") or host.lower().endswith(".localhost"):
        raise SSRFBlockedError(f"blocked hostname: {host}")

    # Literal IP in the URL
    try:
        _assert_public_ip(ipaddress.ip_address(host))
        return
    except ValueError:
        pass

    _assert_hostname_resolves_public(host)


def _assert_hostname_resolves_public(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise FetchError(f"DNS resolution failed for {hostname}") from exc
    if not infos:
        raise FetchError(f"DNS resolution returned no addresses for {hostname}")
    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            _assert_public_ip(ipaddress.ip_address(ip_str))
        except SSRFBlockedError:
            raise
        except ValueError:
            continue


def _assert_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or getattr(ip, "is_site_local", False)
    ):
        raise SSRFBlockedError(f"blocked address: {ip}")
    # Carrier-grade NAT / documentation ranges sometimes missed by is_private
    # depending on Python version; be explicit for common SSRF targets.
    if isinstance(ip, ipaddress.IPv4Address):
        extra_blocked = (
            ipaddress.ip_network("0.0.0.0/8"),
            ipaddress.ip_network("100.64.0.0/10"),
            ipaddress.ip_network("169.254.0.0/16"),
            ipaddress.ip_network("192.0.0.0/24"),
            ipaddress.ip_network("192.0.2.0/24"),
            ipaddress.ip_network("198.18.0.0/15"),
            ipaddress.ip_network("198.51.100.0/24"),
            ipaddress.ip_network("203.0.113.0/24"),
            ipaddress.ip_network("224.0.0.0/4"),
            ipaddress.ip_network("240.0.0.0/4"),
            ipaddress.ip_network("255.255.255.255/32"),
        )
        for network in extra_blocked:
            if ip in network:
                raise SSRFBlockedError(f"blocked address: {ip}")
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in ipaddress.ip_network("fc00::/7") or ip in ipaddress.ip_network("fe80::/10"):
            raise SSRFBlockedError(f"blocked address: {ip}")
