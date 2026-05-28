"""tests/conftest.py — Shared pytest fixtures for signal-loom test suite."""

from pathlib import Path

import feedparser
import pytest

from core import fetch
from core import index


@pytest.fixture
def rss_fixture():
    """Parse the bundled rss_feed.xml fixture with feedparser.

    Returns a feedparser.FeedParserDict — the same type that
    core.fetch.parse_feed returns, so tests can inject it directly as the
    fetch_feed callable's return value.
    """
    xml_text = (Path(__file__).parent / "fixtures" / "rss_feed.xml").read_text()
    return fetch.parse_feed(xml_text)


@pytest.fixture
def index_file(tmp_path):
    """Build an index from the golden corpus into a temp file; return its path."""
    src = Path(__file__).parent / "fixtures/golden/content"
    out = tmp_path / "index.json"
    index.build_index(src, out)
    return out
