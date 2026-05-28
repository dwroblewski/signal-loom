"""tests/test_security.py — Security-behavioural tests for SSRF guard and filename hardening.

Tests:
  SSRF egress guard (_assert_safe_url / BlockedURLError):
    - Private/loopback/link-local/cloud-metadata IPs raise BlockedURLError
    - Non-http(s) schemes raise BlockedURLError
    - A normal public URL does not raise (monkeypatches DNS to avoid real lookups)

  Response size cap:
    - fetch_article_direct returns None when response > 10 MB

  Filename hardening (_sanitize_filename):
    - Null bytes are stripped
    - Leading dots are stripped (no hidden files)
    - Empty-after-strip titles get a placeholder, not an empty string
"""

import socket
from unittest.mock import patch

import pytest

from core.fetch import BlockedURLError, _assert_safe_url, _MAX_RESPONSE_BYTES
from core.scrape import _sanitize_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_getaddrinfo_public(host, port, *args, **kwargs):
    """Simulate DNS returning a public IP (e.g. 93.184.216.34 for example.com)."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def _fake_getaddrinfo_private(host, port, *args, **kwargs):
    """Simulate DNS returning a private/internal IP."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]


def _fake_getaddrinfo_loopback(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]


# ---------------------------------------------------------------------------
# SSRF guard — blocked cases
# ---------------------------------------------------------------------------


def test_ssrf_blocks_link_local_literal():
    """Link-local IP (169.254.169.254) as literal must be blocked without DNS.

    169.254.169.254 is in _BLOCKED_METADATA_HOSTS so it's caught before
    the IP-range check — the error message says "cloud-metadata".
    """
    with pytest.raises(BlockedURLError):
        _assert_safe_url("http://169.254.169.254/latest/meta-data/")


def test_ssrf_blocks_loopback_literal():
    """Loopback literal IP (127.0.0.1) must be blocked."""
    with pytest.raises(BlockedURLError, match="private/internal range"):
        _assert_safe_url("http://127.0.0.1/")


def test_ssrf_blocks_localhost_via_dns():
    """'localhost' resolving to 127.0.0.1 must be blocked via DNS resolution."""
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo_loopback):
        with pytest.raises(BlockedURLError, match="private/internal range"):
            _assert_safe_url("http://localhost:8080/admin")


def test_ssrf_blocks_private_ip_literal():
    """Private-range literal IP (10.x.x.x) must be blocked."""
    with pytest.raises(BlockedURLError, match="private/internal range"):
        _assert_safe_url("http://10.0.0.5/secret")


def test_ssrf_blocks_private_hostname_via_dns():
    """A hostname resolving to 10.x.x.x must be blocked via DNS resolution."""
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo_private):
        with pytest.raises(BlockedURLError, match="private/internal range"):
            _assert_safe_url("https://internal.corp/api")


def test_ssrf_blocks_metadata_google_internal():
    """Known cloud-metadata hostname must be blocked before DNS."""
    with pytest.raises(BlockedURLError, match="cloud-metadata"):
        _assert_safe_url("http://metadata.google.internal/computeMetadata/v1/")


def test_ssrf_blocks_file_scheme():
    """file:// scheme must be rejected."""
    with pytest.raises(BlockedURLError, match="not http/https"):
        _assert_safe_url("file:///etc/passwd")


def test_ssrf_blocks_ftp_scheme():
    """ftp:// scheme must be rejected."""
    with pytest.raises(BlockedURLError, match="not http/https"):
        _assert_safe_url("ftp://x.example.com/file.txt")


def test_ssrf_blocks_192_168_literal():
    """Private class-C (192.168.x.x) literal IP must be blocked."""
    with pytest.raises(BlockedURLError, match="private/internal range"):
        _assert_safe_url("http://192.168.1.1/")


def test_ssrf_blocks_ipv6_loopback():
    """IPv6 loopback (::1) literal must be blocked."""
    with pytest.raises(BlockedURLError, match="private/internal range"):
        _assert_safe_url("http://[::1]/")


# ---------------------------------------------------------------------------
# SSRF guard — allowed cases
# ---------------------------------------------------------------------------


def test_ssrf_allows_public_https():
    """A normal public HTTPS URL must not raise when DNS returns a public IP."""
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo_public):
        _assert_safe_url("https://example.com/article")  # Must not raise


def test_ssrf_allows_public_http():
    """A normal public HTTP URL must not raise when DNS returns a public IP."""
    with patch("socket.getaddrinfo", side_effect=_fake_getaddrinfo_public):
        _assert_safe_url("http://example.com/rss")  # Must not raise


def test_ssrf_fails_open_on_dns_error():
    """When DNS resolution fails, the guard must fail OPEN (no BlockedURLError raised)."""
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("nxdomain")):
        # Should NOT raise — fail open on resolution error.
        _assert_safe_url("https://x.test/article")  # Must not raise


# ---------------------------------------------------------------------------
# Response size cap
# ---------------------------------------------------------------------------


def test_fetch_article_direct_rejects_oversized_response(httpx_mock):
    """fetch_article_direct must return None when the response body exceeds 10 MB."""
    oversized_body = "A" * (_MAX_RESPONSE_BYTES + 1)
    httpx_mock.add_response(url="https://x.test/huge", text=oversized_body)
    from core.fetch import fetch_article_direct
    result = fetch_article_direct("https://x.test/huge")
    assert result is None, "Oversized response should be rejected and return None"


# ---------------------------------------------------------------------------
# Filename hardening
# ---------------------------------------------------------------------------


def test_sanitize_strips_null_bytes():
    """Null bytes in a title must be removed from the filename."""
    result = _sanitize_filename("foo\x00bar")
    assert "\x00" not in result
    assert "foobar" in result or result  # Non-empty, null-free


def test_sanitize_strips_leading_dots():
    """A title starting with dots must not produce a hidden-file name."""
    result = _sanitize_filename("...hidden")
    assert not result.startswith("."), f"Result started with dot: {result!r}"
    assert result  # Non-empty


def test_sanitize_strips_multiple_leading_dots():
    """Multiple leading dots must all be stripped."""
    result = _sanitize_filename("....dotty title here")
    assert not result.startswith(".")
    assert result  # Non-empty


def test_sanitize_null_only_title_gets_placeholder():
    """A title that is only null bytes must produce a safe non-empty placeholder."""
    result = _sanitize_filename("\x00\x00\x00")
    assert result  # Non-empty
    assert "\x00" not in result


def test_sanitize_dot_only_title_gets_placeholder():
    """A title that is only dots must produce a safe non-empty placeholder."""
    result = _sanitize_filename("...")
    assert result  # Non-empty
    assert not result.startswith(".")
