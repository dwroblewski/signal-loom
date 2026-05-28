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
    """
    content_dir = tmp_path / "content" / "smoke-source"
    content_dir.mkdir(parents=True, exist_ok=True)

    sources = {
        "smoke_source": {
            "name": "Smoke Source",
            "type": "rss",
            "feed_url": "https://fixture.example.com/feed",
            "output_dir": str(content_dir),
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


def test_pipeline_smoke(tmp_path):
    """Full pipeline pass with both seams active: rc==0, index has enriched entry."""
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


def test_pipeline_no_enrich(tmp_path):
    """With --no-enrich the index is still rebuilt but no enrichment runs."""
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


def test_pipeline_dry_run(tmp_path):
    """--dry-run returns 0 and does NOT write any files."""
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
