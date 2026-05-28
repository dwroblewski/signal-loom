"""tests/test_brief.py — Tests for core.brief digest builder.

Golden fixture data (from tests/fixtures/golden/index.json):
  Entry 1: "Autonomous AI Agents Reshape Enterprise Workflows"
    url: https://oneusefulthing.substack.com/p/autonomous-agents-enterprise
    published: 2026-05-22
    topics.primary: ["ai agents", "enterprise ai"]

  Entry 2: "OpenAI Releases GPT-5 with Enhanced Reasoning"
    url: https://importai.substack.com/p/openai-releases-gpt5
    published: 2026-05-20
    topics.primary: ["model releases", "ai research"]
"""

import json
from pathlib import Path

import pytest

from core import brief


def _golden_urls() -> list[str]:
    data = json.loads(
        (Path(__file__).parent / "fixtures/golden/index.json").read_text()
    )
    return [e["url"] for e in data["entries"]]


def test_brief_groups_by_primary_topic(index_file):
    """build() without verify produces markdown with ## topic headers."""
    md = brief.build(index_file, since="2026-01-01", verify=False)
    assert md.count("## ") >= 1
    # Both primary topics from entry 1 should appear as headers
    assert "## ai agents" in md or "## AI Agents" in md.lower().replace("## ", "## ")
    # Confirm at least one known primary topic appears
    known_topics = {"ai agents", "enterprise ai", "model releases", "ai research"}
    headers_found = [
        line.lstrip("## ").strip().lower()
        for line in md.splitlines()
        if line.startswith("## ")
    ]
    assert any(h in known_topics for h in headers_found), (
        f"No known primary topic found in headers: {headers_found}"
    )


def test_brief_contains_titles_and_sources(index_file):
    """Each entry's title and source appear in the output."""
    md = brief.build(index_file, since="2026-01-01", verify=False)
    assert "Autonomous AI Agents Reshape Enterprise Workflows" in md
    assert "OpenAI Releases GPT-5 with Enhanced Reasoning" in md
    assert "one-useful-thing" in md
    assert "import-ai" in md


def test_brief_verifies_urls(index_file, httpx_mock):
    """With verify=True all live URLs report 'live' in last_verification()."""
    for u in _golden_urls():
        httpx_mock.add_response(method="HEAD", url=u, status_code=200)
    brief.build(index_file, since="2026-01-01", verify=True)
    status = brief.last_verification()
    assert status, "last_verification() must be non-empty after a verify=True build"
    assert all(v == "live" for v in status.values()), (
        f"Expected all live, got: {status}"
    )


def test_brief_marks_dead_link(index_file, httpx_mock):
    """A 404 response causes the URL to be classified as 'dead'."""
    urls = _golden_urls()
    httpx_mock.add_response(method="HEAD", url=urls[0], status_code=404)
    for u in urls[1:]:
        httpx_mock.add_response(method="HEAD", url=u, status_code=200)
    brief.build(index_file, since="2026-01-01", verify=True)
    assert brief.last_verification()[urls[0]] == "dead"


def test_brief_marks_stale_on_network_error(index_file, httpx_mock):
    """A network error (ConnectError) causes the URL to be classified as 'stale'."""
    import httpx as _httpx

    urls = _golden_urls()
    httpx_mock.add_exception(
        _httpx.ConnectError("connection refused"), method="HEAD", url=urls[0]
    )
    for u in urls[1:]:
        httpx_mock.add_response(method="HEAD", url=u, status_code=200)
    brief.build(index_file, since="2026-01-01", verify=True)
    assert brief.last_verification()[urls[0]] == "stale"


def test_brief_annotates_live_in_markdown(index_file, httpx_mock):
    """Verify annotations appear in the rendered markdown."""
    for u in _golden_urls():
        httpx_mock.add_response(method="HEAD", url=u, status_code=200)
    md = brief.build(index_file, since="2026-01-01", verify=True)
    assert "✓" in md or "live" in md.lower()


def test_brief_annotates_dead_in_markdown(index_file, httpx_mock):
    """Dead links get a dead annotation in the markdown."""
    urls = _golden_urls()
    httpx_mock.add_response(method="HEAD", url=urls[0], status_code=404)
    for u in urls[1:]:
        httpx_mock.add_response(method="HEAD", url=u, status_code=200)
    md = brief.build(index_file, since="2026-01-01", verify=True)
    assert "✗" in md or "dead" in md.lower()


def test_last_verification_empty_before_build():
    """last_verification() returns an empty dict before any verify=True build."""
    brief._state["verification"] = {}
    result = brief.last_verification()
    assert result == {}


def test_brief_no_verify_does_not_head_check(index_file, httpx_mock):
    """With verify=False, no HTTP requests are made (httpx_mock would raise on unexpected calls)."""
    # If any HEAD call is made, httpx_mock will raise since no responses are registered
    md = brief.build(index_file, since="2026-01-01", verify=False)
    assert isinstance(md, str)
    assert len(md) > 0
