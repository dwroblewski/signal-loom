"""Tests for core/fetch.py — network fetch layer characterization tests.

Three tests required by spec:
  1. test_direct_fetch_extracts_body  — httpx mock → trafilatura extraction
  2. test_rss_parse_returns_entries   — pure feedparser.parse wrapper
  3. test_browser_fetch_actionable_error_without_extra — BrowserExtraMissing raised

Note on test_direct_fetch_extracts_body assertion adaptation:
  The spec checks `"<script" not in body`. trafilatura strips all HTML tags during
  extraction so this will always hold for clean text output. No adaptation needed —
  trafilatura extraction on the ssr_article.html fixture reliably yields >200 chars.

Note on DNS mocking:
  _assert_safe_url() now fails CLOSED on DNS error (fix #4). Tests that use
  httpx_mock with synthetic hostnames (x.test) must also monkeypatch
  socket.getaddrinfo to return a public IP so the SSRF guard passes.
"""
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from core import fetch

FX = Path(__file__).parent / "fixtures"

_FAKE_PUBLIC_ADDRINFO = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def test_direct_fetch_extracts_body(httpx_mock):
    httpx_mock.add_response(url="https://x.test/a", text=(FX / "ssr_article.html").read_text())
    with patch("socket.getaddrinfo", return_value=_FAKE_PUBLIC_ADDRINFO):
        body = fetch.fetch_article_direct("https://x.test/a")
    assert body and len(body) > 200 and "<script" not in body


def test_rss_parse_returns_entries():
    feed = fetch.parse_feed((FX / "rss_feed.xml").read_text())
    assert len(feed.entries) >= 1 and feed.entries[0].title


def test_browser_fetch_actionable_error_without_extra(monkeypatch):
    monkeypatch.setattr(fetch, "_PLAYWRIGHT_AVAILABLE", False)
    monkeypatch.setattr(fetch, "fetch_article_direct", lambda *a, **kw: None)
    with pytest.raises(fetch.BrowserExtraMissing) as e:
        fetch.fetch_article_with_browser("https://x.test/a")
    assert "uv sync --extra browser" in str(e.value)


# ---------------------------------------------------------------------------
# Fix #2 — Browser fetch SSRF guard
# ---------------------------------------------------------------------------


def test_browser_fetch_calls_assert_safe_url_before_launch(monkeypatch):
    """_fetch_browser_html must call _assert_safe_url before launching the browser.

    If _assert_safe_url raises BlockedURLError the function must propagate it
    (or the caller must handle it) — the browser must NOT be launched.
    """
    # Monkeypatch _assert_safe_url to raise on the given URL.
    blocked_calls: list[str] = []

    def _blocking_assert(url: str) -> None:
        blocked_calls.append(url)
        raise fetch.BlockedURLError(f"test block: {url}")

    monkeypatch.setattr(fetch, "_assert_safe_url", _blocking_assert)
    monkeypatch.setattr(fetch, "_PLAYWRIGHT_AVAILABLE", True)

    # _fetch_browser_html should raise BlockedURLError (propagated from guard),
    # NOT launch a browser.
    with pytest.raises(fetch.BlockedURLError):
        fetch._fetch_browser_html("http://169.254.169.254/meta-data/")

    assert blocked_calls, "_assert_safe_url must have been called"


def test_fetch_html_with_browser_blocked_url(monkeypatch):
    """fetch_html_with_browser must propagate BlockedURLError from the guard."""
    monkeypatch.setattr(
        fetch, "_assert_safe_url",
        lambda url: (_ for _ in ()).throw(fetch.BlockedURLError("blocked")),
    )
    monkeypatch.setattr(fetch, "_PLAYWRIGHT_AVAILABLE", True)
    with pytest.raises(fetch.BlockedURLError):
        fetch.fetch_html_with_browser("http://10.0.0.1/")


# ---------------------------------------------------------------------------
# Fix #4 — Fail-CLOSED on DNS error
# ---------------------------------------------------------------------------


def test_assert_safe_url_fails_closed_on_dns_error():
    """_assert_safe_url must raise BlockedURLError when DNS resolution fails.

    An unresolvable hostname cannot be verified safe, so the guard must refuse.
    """
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("nxdomain")):
        with pytest.raises(fetch.BlockedURLError, match="DNS resolution failed"):
            fetch._assert_safe_url("https://totally-unresolvable-host.example/")


def test_fetch_article_direct_blocked_on_dns_failure():
    """fetch_article_direct returns None (not crash) when the host fails DNS.

    The SSRF guard raises BlockedURLError; fetch_article_direct catches it
    and returns None.
    """
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("nxdomain")):
        result = fetch.fetch_article_direct("https://totally-unresolvable-host.example/")
    assert result is None
