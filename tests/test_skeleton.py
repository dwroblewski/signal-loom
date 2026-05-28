"""End-to-end contract the whole pipeline must satisfy.

Stays SKIPPED until core.pipeline exists (Task D3), at which point it goes
green and becomes the integration gate proving config→scrape→enrich→
writeback→index seams line up. See plan Task B0.
"""
import json
import pytest


@pytest.mark.skeleton
def test_endtoend_contract(tmp_path, monkeypatch):
    pytest.importorskip("core.pipeline")  # skips until D3
    from core import pipeline

    monkeypatch.chdir(tmp_path)  # so relative output_dir in sources.yaml resolves
    rc = pipeline.main([
        "--config", _stub_config(tmp_path),
        "--once",
        "--_inject-fetch", "fixture",
        "--_inject-enricher", "fake",
    ])
    assert rc == 0
    idx = json.loads((tmp_path / "index.json").read_text())
    assert idx["entries"] and idx["entries"][0]["enriched"] is True


def _stub_config(tmp_path) -> str:
    """Build a minimal but valid signal-loom config tree under *tmp_path*.

    Writes:
    - ``sources.yaml``        — one enabled RSS source
    - ``topics.yaml``         — a small vocabulary incl. "ai agents"
    - ``entity-aliases.yaml`` — empty mapping
    - ``signal-loom.yaml``    — settings pointing into tmp_path

    Returns the path to ``signal-loom.yaml`` as a str.
    """
    import yaml

    content_dir = tmp_path / "content" / "test-source"
    content_dir.mkdir(parents=True, exist_ok=True)

    # sources.yaml — one RSS source; output_dir inside tmp_path
    sources = {
        "test_source": {
            "name": "Test Source",
            "type": "rss",
            "feed_url": "https://fixture.example.com/feed",
            # Relative path — passes load_sources containment check (fix #6).
            # Resolves against CWD, which callers set to tmp_path via monkeypatch.chdir().
            "output_dir": "content/test-source",
            "tags": ["ai"],
            "scrape_limit": 5,
            "enabled": True,
        }
    }
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.dump(sources))

    # topics.yaml — vocabulary; "ai agents" is what _FakeEnricher picks first
    topics = ["ai agents", "model releases", "ai policy", "ai safety", "enterprise ai"]
    topics_path = tmp_path / "topics.yaml"
    topics_path.write_text(yaml.dump(topics))

    # entity-aliases.yaml — empty is fine
    aliases_path = tmp_path / "entity-aliases.yaml"
    aliases_path.write_text("{}\n")

    # signal-loom.yaml — global settings
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
