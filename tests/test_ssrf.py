"""SSRF and fetch safety unit tests."""

from __future__ import annotations

import pytest

from deeprhetor.services.fetch import SSRFBlockedError, validate_public_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "https://127.0.0.1:8443/x",
        "http://localhost/admin",
        "http://[::1]/",
        "http://10.0.0.5/secret",
        "http://10.255.255.1/",
        "http://192.168.1.10/page",
        "http://192.168.0.1/",
        "http://172.16.0.1/",
        "http://172.31.255.255/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "http://192.0.2.1/",
    ],
)
def test_validate_public_url_blocks_ssrf_targets(url: str) -> None:
    with pytest.raises(SSRFBlockedError):
        validate_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://1.1.1.1/",
        "https://8.8.8.8/path",
    ],
)
def test_validate_public_url_allows_public_hosts(url: str) -> None:
    validate_public_url(url)
