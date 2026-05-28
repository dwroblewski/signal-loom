"""tests/test_scrape.py — Tests for core.scrape adapters.

Tests confirm:
  - RSS adapter writes valid YAML frontmatter markdown (not line-1 hashtags)
  - keyword_filter drops off-topic items
  - YouTube captions adapter writes valid YAML frontmatter markdown
  - Atom/ISO 8601 dates are parsed correctly (not silently replaced by today)
  - Long-prefix title dedup: two items with shared ≥100-char prefix both get written
  - scrape_full_content=True calls fetch_article for each passing item
  - Listing adapter extracts article links, filters, and writes frontmatter markdown
  - Listing adapter applies keyword_filter BEFORE fetch_article (pre-filter)
"""

import json
from pathlib import Path

import feedparser

from core import config, fetch, scrape

FX = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rss_src(tmp_path, **kwargs):
    defaults = dict(
        name="X",
        type="rss",
        feed_url="f",
        output_dir=str(tmp_path),
        tags=["newsletter", "x"],
        scrape_limit=10,
    )
    defaults.update(kwargs)
    return config.SourceConfig(**defaults)


# ---------------------------------------------------------------------------
# RSS / Atom — existing behaviour
# ---------------------------------------------------------------------------


def test_rss_writes_frontmatter_markdown(tmp_path, rss_fixture):
    src = _make_rss_src(tmp_path, scrape_limit=5)
    written = scrape.run_source(
        src,
        fetch_feed=lambda u: rss_fixture,
        fetch_article=lambda u: "body text " * 60,
    )
    assert written, "Expected at least one file written"
    md = written[0].read_text()
    assert md.startswith("---"), "File must start with YAML frontmatter delimiter"
    assert "tags:" in md, "Frontmatter must contain tags key"
    assert not md.splitlines()[0].startswith("#"), "First line must not be a hashtag"


def test_keyword_filter_drops_offtopic(tmp_path, rss_fixture):
    src = _make_rss_src(
        tmp_path,
        keyword_filter={"mode": "any", "include": ["zzzznomatch"]},
    )
    result = scrape.run_source(
        src,
        fetch_feed=lambda u: rss_fixture,
        fetch_article=lambda u: "body " * 60,
    )
    assert result == [], "All items should be filtered out by keyword_filter"


# ---------------------------------------------------------------------------
# Fix 1: Atom/ISO date parsing
# ---------------------------------------------------------------------------


def test_atom_iso_date_parsed_correctly(tmp_path):
    """Atom entries with ISO 8601 published dates must get REAL date, not today."""
    xml_text = (FX / "atom_feed.xml").read_text()
    atom_parsed = fetch.parse_feed(xml_text)

    src = _make_rss_src(tmp_path, scrape_limit=5)
    written = scrape.run_source(src, fetch_feed=lambda u: atom_parsed)

    assert written, "Expected at least one file written from Atom feed"
    # Both entries must have real publish dates in their filenames
    names = [p.name for p in written]
    # Entry 1: published 2026-05-20T14:30:00Z → 2026-05-20
    assert any(n.startswith("2026-05-20") for n in names), (
        f"Expected file with date 2026-05-20 from Atom ISO timestamp; got {names}"
    )
    # Entry 2: published 2026-05-15 (plain ISO date)
    assert any(n.startswith("2026-05-15") for n in names), (
        f"Expected file with date 2026-05-15 from Atom plain ISO date; got {names}"
    )


# ---------------------------------------------------------------------------
# Fix 2: Truncation collision / URL hash disambiguation
# ---------------------------------------------------------------------------

_LONG_PREFIX = "A" * 95  # after sanitize_filename this stays >100 chars before truncation


def _make_long_prefix_feed():
    """Return a feedparser-like dict with two entries sharing a ≥100-char prefix."""
    title_a = _LONG_PREFIX + " First Article About Machine Learning"
    title_b = _LONG_PREFIX + " Second Article About Deep Learning"
    # Build a minimal Atom feed so feedparser gives us published_parsed
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test</title>
  <entry>
    <title>{title_a}</title>
    <link href="https://example.com/article-first-aaa111"/>
    <id>https://example.com/article-first-aaa111</id>
    <published>2026-05-20T00:00:00Z</published>
    <summary>Content about machine learning systems and their applications in practice today.</summary>
  </entry>
  <entry>
    <title>{title_b}</title>
    <link href="https://example.com/article-second-bbb222"/>
    <id>https://example.com/article-second-bbb222</id>
    <published>2026-05-20T00:00:00Z</published>
    <summary>Content about deep learning architectures and training procedures for modern models.</summary>
  </entry>
</feed>"""
    import feedparser as _fp
    return _fp.parse(xml)


def test_long_prefix_collision_both_written(tmp_path):
    """Two entries sharing a ≥100-char title prefix must both produce files."""
    src = _make_rss_src(tmp_path, scrape_limit=10)
    parsed = _make_long_prefix_feed()
    written = scrape.run_source(src, fetch_feed=lambda u: parsed)

    assert len(written) == 2, (
        f"Expected both collision-prefix items written; got {len(written)}: {[p.name for p in written]}"
    )


# ---------------------------------------------------------------------------
# Fix 7: scrape_full_content=True exercises fetch_article
# ---------------------------------------------------------------------------


def test_scrape_full_content_calls_fetch_article(tmp_path, rss_fixture):
    """fetch_article must be invoked when scrape_full_content=True."""
    called_urls = []

    def capturing_fetch_article(url):
        called_urls.append(url)
        return "full body content " * 60

    src = _make_rss_src(tmp_path, scrape_full_content=True)
    written = scrape.run_source(
        src,
        fetch_feed=lambda u: rss_fixture,
        fetch_article=capturing_fetch_article,
    )
    assert written, "Expected files written"
    assert called_urls, "fetch_article should have been called with scrape_full_content=True"
    # Confirm full body ended up in the file
    assert "full body content" in written[0].read_text()


# ---------------------------------------------------------------------------
# YouTube adapter
# ---------------------------------------------------------------------------


def test_youtube_captions_to_markdown(tmp_path):
    src = config.SourceConfig(
        name="Y",
        type="youtube",
        feed_url="https://yt/@x",
        output_dir=str(tmp_path),
        tags=["youtube"],
        scrape_limit=2,
    )
    caps = json.loads((FX / "yt_captions.json").read_text())
    written = scrape.run_source(src, fetch_youtube=lambda u, n: caps)
    assert written, "Expected at least one file written"
    assert written[0].read_text().startswith("---"), "YouTube file must start with YAML frontmatter"


# ---------------------------------------------------------------------------
# Fix 6: Listing adapter
# ---------------------------------------------------------------------------


def _listing_article_body(url: str) -> str:
    """Inject a realistic article body for listing tests."""
    slug = url.rstrip("/").split("/")[-1]
    title = slug.replace("-", " ").title()
    return (
        f"# {title}\n\n2026-05-20\n\n"
        + ("This is the article body content covering important research topics. " * 20)
    )


def test_listing_adapter_extracts_and_writes(tmp_path):
    """Listing adapter extracts article links, fetches each, and writes frontmatter MD."""
    listing_html = (FX / "listing_index.html").read_text()

    src = config.SourceConfig(
        name="ResearchSite",
        type="listing",
        feed_url="https://research.example.com/",
        output_dir=str(tmp_path),
        tags=["research"],
        scrape_limit=10,
    )

    fetch_article_calls = []

    def mock_fetch_article(url):
        fetch_article_calls.append(url)
        return _listing_article_body(url)

    written = scrape.run_source(
        src,
        fetch_listing=lambda u: listing_html,
        fetch_article=mock_fetch_article,
    )

    # Should have extracted the 4 article/news links (not home/about/contact/feed/login/signup)
    assert len(written) >= 3, f"Expected ≥3 article files; got {len(written)}: {[p.name for p in written]}"
    assert fetch_article_calls, "fetch_article should be called for extracted links"

    # All written files must start with YAML frontmatter
    for p in written:
        md = p.read_text()
        assert md.startswith("---"), f"{p.name} must start with YAML frontmatter"
        assert "source:" in md, f"{p.name} must contain source field"


def test_listing_adapter_keyword_filter_before_fetch(tmp_path):
    """Listing adapter must NOT call fetch_article for links that fail keyword_filter."""
    listing_html = (FX / "listing_index.html").read_text()

    src = config.SourceConfig(
        name="ResearchSite",
        type="listing",
        feed_url="https://research.example.com/",
        output_dir=str(tmp_path),
        tags=["research"],
        scrape_limit=10,
        # Only match articles about "transformer" — only one link slug contains it
        keyword_filter={"mode": "any", "include": ["transformer"]},
    )

    fetch_article_calls = []

    def tracking_fetch_article(url):
        fetch_article_calls.append(url)
        return _listing_article_body(url)

    written = scrape.run_source(
        src,
        fetch_listing=lambda u: listing_html,
        fetch_article=tracking_fetch_article,
    )

    # Only the transformer article should pass the filter
    assert len(written) == 1, (
        f"Only 1 article should pass 'transformer' filter; got {len(written)}: {[p.name for p in written]}"
    )
    # fetch_article should only be called for the matching article
    assert len(fetch_article_calls) == 1, (
        f"fetch_article should only be called once (pre-filter); got {len(fetch_article_calls)} calls: {fetch_article_calls}"
    )
    assert "transformer" in fetch_article_calls[0].lower()


# ---------------------------------------------------------------------------
# Fix #5 — yt-dlp host whitelist
# ---------------------------------------------------------------------------


def test_youtube_adapter_rejects_non_youtube_feed_url(tmp_path):
    """YouTube adapter must reject feed_url that is not a YouTube host.

    If a non-YouTube URL (e.g. an SSRF target) is supplied as feed_url,
    _default_fetch_youtube must return [] and must NOT invoke yt-dlp
    (which would pass the arbitrary URL to a subprocess).
    """
    import subprocess as _subprocess
    from unittest.mock import patch

    src = config.SourceConfig(
        name="EvilYT",
        type="youtube",
        feed_url="http://169.254.169.254/latest/meta-data/",
        output_dir=str(tmp_path),
        tags=["test"],
    )

    # If subprocess.run is called, the test should fail loudly.
    with patch.object(_subprocess, "run", side_effect=AssertionError("yt-dlp must not be called for non-YouTube URLs")):
        result = scrape._default_fetch_youtube("http://169.254.169.254/latest/meta-data/", limit=5)

    assert result == [], "Non-YouTube feed_url must return empty list, not call yt-dlp"


def test_youtube_adapter_rejects_http_youtube_url(tmp_path):
    """YouTube adapter must reject http:// (non-https) YouTube URLs."""
    import subprocess as _subprocess
    from unittest.mock import patch

    with patch.object(_subprocess, "run", side_effect=AssertionError("yt-dlp must not be called for http:// URLs")):
        result = scrape._default_fetch_youtube("http://www.youtube.com/@channel", limit=5)

    assert result == [], "http:// YouTube URL must be rejected"


def test_youtube_adapter_accepts_valid_youtube_url(tmp_path, monkeypatch):
    """YouTube adapter must pass valid https://www.youtube.com/... to yt-dlp."""
    import subprocess as _subprocess

    # Simulate yt-dlp returning zero results (empty output) so we can verify
    # it was called without actually running it.
    ytdlp_called: list[str] = []

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        ytdlp_called.append(cmd[-1])  # last arg is the channel URL
        return _FakeResult()

    monkeypatch.setattr(_subprocess, "run", fake_run)

    # youtube-transcript-api is also needed — patch the import
    import sys
    import types
    _fake_ytt = types.ModuleType("youtube_transcript_api")
    _fake_ytt.YouTubeTranscriptApi = object
    _fake_errors = types.ModuleType("youtube_transcript_api._errors")
    _fake_errors.NoTranscriptFound = Exception
    _fake_errors.TranscriptsDisabled = Exception
    _fake_errors.VideoUnavailable = Exception
    monkeypatch.setitem(sys.modules, "youtube_transcript_api", _fake_ytt)
    monkeypatch.setitem(sys.modules, "youtube_transcript_api._errors", _fake_errors)

    valid_url = "https://www.youtube.com/@TestChannel"
    result = scrape._default_fetch_youtube(valid_url, limit=5)

    assert ytdlp_called == [valid_url], (
        f"yt-dlp should be called with the valid URL; got: {ytdlp_called}"
    )
    assert result == [], "Empty yt-dlp output → empty result list"
