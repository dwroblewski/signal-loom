"""tests/test_pipeline.py — Smoke tests for core.pipeline (non-skeleton).

These tests run in the default pytest suite (no -m flag required).
They exercise the full pipeline.main() path using both test seams:
  --_inject-fetch fixture   → synthetic one-item RSS feed, no network I/O
  --_inject-enricher fake   → fixed valid YAML response, no API key required
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core import pipeline


# ---------------------------------------------------------------------------
# Shared fixture builder
# ---------------------------------------------------------------------------


def _build_stub_config(tmp_path: Path) -> str:
    """Write a minimal signal-loom config tree under *tmp_path*.

    Returns the path to ``signal-loom.yaml`` as a str.

    ``output_dir`` in sources.yaml is written as a relative path
    (``content/smoke-source``) so it passes the load_sources containment check
    (fix #6: absolute paths are rejected).  Callers must chdir to *tmp_path*
    before invoking pipeline.main() so the relative path resolves correctly.
    """
    content_dir = tmp_path / "content" / "smoke-source"
    content_dir.mkdir(parents=True, exist_ok=True)

    sources = {
        "smoke_source": {
            "name": "Smoke Source",
            "type": "rss",
            "feed_url": "https://fixture.example.com/feed",
            # Relative path — passes containment check; resolves against CWD
            # which callers set to tmp_path via monkeypatch.chdir().
            "output_dir": "content/smoke-source",
            "tags": ["ai"],
            "scrape_limit": 5,
            "enabled": True,
        }
    }
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.dump(sources))

    topics = ["ai agents", "model releases", "ai policy", "ai safety", "enterprise ai"]
    topics_path = tmp_path / "topics.yaml"
    topics_path.write_text(yaml.dump(topics))

    aliases_path = tmp_path / "entity-aliases.yaml"
    aliases_path.write_text("{}\n")

    settings = {
        "enrichment_model": "claude-sonnet-4-6",
        # content_dir and index_path in signal-loom.yaml are Settings, not
        # SourceConfig.output_dir — the absolute-path check does not apply there.
        "content_dir": str(tmp_path / "content"),
        "index_path": str(tmp_path / "index.json"),
        "sources_path": str(sources_path),
        "topics_path": str(topics_path),
        "aliases_path": str(aliases_path),
    }
    config_path = tmp_path / "signal-loom.yaml"
    config_path.write_text(yaml.dump(settings))
    return str(config_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pipeline_smoke(tmp_path, monkeypatch):
    """Full pipeline pass with both seams active: rc==0, index has enriched entry."""
    monkeypatch.chdir(tmp_path)  # so relative output_dir resolves correctly
    config = _build_stub_config(tmp_path)

    rc = pipeline.main([
        "--config", config,
        "--once",
        "--_inject-fetch", "fixture",
        "--_inject-enricher", "fake",
    ])

    assert rc == 0

    index_path = tmp_path / "index.json"
    assert index_path.exists(), "index.json should be written"

    idx = json.loads(index_path.read_text())
    assert "entries" in idx, "index must have 'entries' key"
    assert len(idx["entries"]) >= 1, "at least one enriched entry expected"
    assert idx["entries"][0]["enriched"] is True, "first entry must have enriched=True"


def test_pipeline_no_enrich(tmp_path, monkeypatch):
    """With --no-enrich the index is still rebuilt but no enrichment runs."""
    monkeypatch.chdir(tmp_path)  # so relative output_dir resolves correctly
    config = _build_stub_config(tmp_path)

    rc = pipeline.main([
        "--config", config,
        "--once",
        "--no-enrich",
        "--_inject-fetch", "fixture",
    ])

    assert rc == 0
    # index.json is written even with --no-enrich
    assert (tmp_path / "index.json").exists()


def test_pipeline_dry_run(tmp_path, monkeypatch):
    """--dry-run returns 0 and does NOT write any files."""
    monkeypatch.chdir(tmp_path)  # so relative output_dir resolves correctly
    config = _build_stub_config(tmp_path)

    rc = pipeline.main([
        "--config", config,
        "--once",
        "--dry-run",
        "--_inject-fetch", "fixture",
        "--_inject-enricher", "fake",
    ])

    assert rc == 0
    # No index written in dry-run
    assert not (tmp_path / "index.json").exists()


def test_pipeline_missing_config(tmp_path):
    """Returns 1 (not an exception) when the config file is absent."""
    rc = pipeline.main([
        "--config", str(tmp_path / "nonexistent.yaml"),
        "--once",
    ])
    assert rc == 1


def test_fake_enricher_vocab_topic():
    """_FakeEnricher picks the first sorted topic from the supplied vocabulary."""
    vocab = {"zebra topic", "ai agents", "model releases"}
    fake = pipeline._FakeEnricher(vocab)
    raw, usage = fake.enrich("some content", vocab)
    # The first sorted topic is "ai agents"
    assert "ai agents" in raw
    assert "enriched: true" in raw
    assert usage["total_input_tokens"] == 0


# ---------------------------------------------------------------------------
# Fix #7 — Enrichment backlog drain
# ---------------------------------------------------------------------------


def test_pipeline_enriches_preexisting_unenriched_file(tmp_path, monkeypatch):
    """Pre-existing unenriched *.md files must be enriched even when scraping yields 0 new files.

    This test:
      1. Places a pre-existing unenriched markdown file in content_dir BEFORE running.
      2. Runs the pipeline with --_inject-fetch fixture AND --_inject-enricher fake,
         but the pre-existing file was not scraped this run (dedup would skip it).
      3. Asserts the pre-existing file has been enriched (enriched: true in frontmatter).
    """
    import frontmatter as _fm

    monkeypatch.chdir(tmp_path)  # so relative output_dir resolves correctly
    config_path = _build_stub_config(tmp_path)

    # Plant a pre-existing unenriched file directly in content_dir (not scraped this run).
    preexisting_dir = tmp_path / "content" / "other-source"
    preexisting_dir.mkdir(parents=True, exist_ok=True)
    preexisting_md = preexisting_dir / "preexisting-article.md"
    preexisting_md.write_text(
        "---\n"
        "title: Pre-existing Article\n"
        "source: other-source\n"
        "url: https://example.com/pre\n"
        "published: '2026-05-01'\n"
        "---\n"
        "This is an article that was scraped in a previous run but never enriched.\n"
        + ("body content " * 50)
    )

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--_inject-fetch", "fixture",
        "--_inject-enricher", "fake",
    ])

    assert rc == 0, "Pipeline should succeed"

    # The pre-existing file must now have enriched: true.
    post = _fm.load(str(preexisting_md))
    assert post.metadata.get("enriched") is True, (
        "Pre-existing unenriched file must be enriched in the same pipeline run"
    )
