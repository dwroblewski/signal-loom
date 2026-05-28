"""Tests for core/fetch.py — network fetch layer characterization tests.

Three tests required by spec:
  1. test_direct_fetch_extracts_body  — httpx mock → trafilatura extraction
  2. test_rss_parse_returns_entries   — pure feedparser.parse wrapper
  3. test_browser_fetch_actionable_error_without_extra — BrowserExtraMissing raised

Note on test_direct_fetch_extracts_body assertion adaptation:
  The spec checks `"<script" not in body`. trafilatura strips all HTML tags during
  extraction so this will always hold for clean text output. No adaptation needed —
  trafilatura extraction on the ssr_article.html fixture reliably yields >200 chars.
"""
from pathlib import Path

import pytest

from core import fetch

FX = Path(__file__).parent / "fixtures"


def test_direct_fetch_extracts_body(httpx_mock):
    httpx_mock.add_response(url="https://x.test/a", text=(FX / "ssr_article.html").read_text())
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
