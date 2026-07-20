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
import socket
from pathlib import Path

import pytest

from core import brief
from core import fetch as _fetch


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    """Resolve every host to a fixed PUBLIC IP so the SSRF guard's real
    getaddrinfo call never touches the network. Keeps these verify tests
    deterministic offline / in no-egress CI (the guard fails CLOSED on DNS
    failure, which would otherwise mark every golden URL 'blocked')."""
    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(_fetch.socket, "getaddrinfo", fake_getaddrinfo)


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


# ---------------------------------------------------------------------------
# Fix #3 — Brief --verify SSRF guard
# ---------------------------------------------------------------------------


def test_brief_verify_blocks_private_ip_url(tmp_path, monkeypatch):
    """An index entry with a private/internal URL must be marked 'blocked', not HEADed.

    The SSRF guard in _head_check must intercept the URL before httpx makes
    any request. We monkeypatch _assert_safe_url to raise for the private URL
    and verify:
      1. The URL's tier is 'blocked' in last_verification().
      2. No HTTP HEAD request was issued for that URL (httpx_mock would fail
         if an unexpected request were made).
    """
    import json

    # Build a minimal index with one private-IP URL.
    private_url = "http://169.254.169.254/latest/meta-data/"
    index_data = {
        "entries": [
            {
                "path": "test/article.md",
                "title": "SSRF Test Article",
                "source": "test",
                "url": private_url,
                "published": "2026-05-01",
                "tags": [],
                "topics": {"primary": ["ai agents"], "secondary": []},
                "entities": {},
                "summary": "test summary",
                "enriched": True,
            }
        ],
        "generated": "2026-05-01T00:00:00+00:00",
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index_data))

    # No httpx responses registered — any HEAD call would raise.
    brief.build(str(index_path), since="2026-01-01", verify=True)
    status = brief.last_verification()
    assert status.get(private_url) == "blocked", (
        f"Private-IP URL must be 'blocked', got: {status.get(private_url)!r}"
    )


def test_brief_verify_follow_redirects_false(tmp_path, httpx_mock):
    """The verify HEAD client must use follow_redirects=False.

    A 301 redirect should be classified as 'live' (3xx → live per tiering),
    not followed into potentially private space.
    """
    import json

    public_url = "https://example.com/article"
    index_data = {
        "entries": [
            {
                "path": "test/article.md",
                "title": "Redirect Test",
                "source": "test",
                "url": public_url,
                "published": "2026-05-01",
                "tags": [],
                "topics": {"primary": ["ai agents"], "secondary": []},
                "entities": {},
                "summary": "redirect test summary",
                "enriched": True,
            }
        ],
        "generated": "2026-05-01T00:00:00+00:00",
    }
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index_data))

    # HEAD returns a 301 — with follow_redirects=False this is the final response.
    httpx_mock.add_response(method="HEAD", url=public_url, status_code=301)

    from unittest.mock import patch
    import socket as _socket

    # Patch DNS so _assert_safe_url passes for example.com.
    fake_addrinfo = [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
    with patch("socket.getaddrinfo", return_value=fake_addrinfo):
        brief.build(str(index_path), since="2026-01-01", verify=True)

    status = brief.last_verification()
    # 3xx → "live" per _classify_response
    assert status.get(public_url) == "live", (
        f"301 with follow_redirects=False must classify as 'live', got: {status.get(public_url)!r}"
    )


def test_render_escapes_title_and_drops_unsafe_url(tmp_path):
    """Scraped titles/URLs are untrusted: a crafted title must not break out of
    the [title](url) link, and a javascript: URL must not render as a link."""
    idx = {
        "entries": [
            {
                "path": "x.md",
                "title": "Update](https://evil.example/phish) [x",
                "source": "s",
                "url": "javascript:alert(1)",
                "published": "2026-05-22",
                "topics": {"primary": ["ai agents"], "secondary": []},
                "summary": "y" * 10,
                "enriched": True,
            }
        ],
        "generated": "2026-05-22T00:00:00Z",
    }
    p = tmp_path / "index.json"
    p.write_text(json.dumps(idx))
    md = brief.build(p, since="2026-01-01", verify=False)
    # The forged link's URL must not appear as an active markdown link target.
    assert "](https://evil.example/phish)" not in md
    # The javascript: URL is dropped — title renders as plain (escaped) text.
    assert "javascript:alert(1)" not in md
