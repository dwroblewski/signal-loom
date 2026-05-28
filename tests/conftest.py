"""tests/conftest.py — Shared pytest fixtures for signal-loom test suite."""

from pathlib import Path

import feedparser
import pytest

from core import fetch


@pytest.fixture
def rss_fixture():
    """Parse the bundled rss_feed.xml fixture with feedparser.

    Returns a feedparser.FeedParserDict — the same type that
    core.fetch.parse_feed returns, so tests can inject it directly as the
    fetch_feed callable's return value.
    """
    xml_text = (Path(__file__).parent / "fixtures" / "rss_feed.xml").read_text()
    return fetch.parse_feed(xml_text)
