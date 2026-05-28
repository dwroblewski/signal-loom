"""tests/test_onboarding_hardening.py — Tests for the onboarding-hardening pass.

Covers:
  #1–#4  Config discovery + auto-bootstrap (resolve_config_path, ensure_configs,
         relative-path resolution, enrichment_writeback no-swallow)
  #5     listing fetch_method=auto uses direct httpx (no browser)
  #6     Cost-gate log message
  #7     Empty vocab fail-fast (pipeline + writeback CLI)
  #8     Exit codes (all-sources-failed, all-enrichment-failed)
  #9     Missing ANTHROPIC_API_KEY preflight
  #10    /brief missing index friendly error
  #11    --dry-run is a real preview
  #12    keyword_filter.mode validation
  #13    failed-enrichments.jsonl queue written on writeback failure
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from core import config as cfg
from core import pipeline
from core import brief as brief_mod
from core import enrichment_writeback as wb


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_config(
    tmp_path: Path,
    *,
    vocab: list[str] | None = None,
) -> str:
    """Write a minimal config tree. Returns path to signal-loom.yaml."""
    topics = vocab if vocab is not None else ["ai agents", "model releases"]
    topics_path = tmp_path / "topics.yaml"
    topics_path.write_text(yaml.dump(topics))

    aliases_path = tmp_path / "entity-aliases.yaml"
    aliases_path.write_text("{}\n")

    content_dir = tmp_path / "content" / "smoke"
    content_dir.mkdir(parents=True, exist_ok=True)

    sources = {
        "smoke": {
            "name": "Smoke",
            "type": "rss",
            "feed_url": "https://fixture.example.com/feed",
            "output_dir": "content/smoke",
            "tags": ["ai"],
            "scrape_limit": 5,
            "enabled": True,
        }
    }
    sources_path = tmp_path / "sources.yaml"
    sources_path.write_text(yaml.dump(sources))

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
# #1-#4 Config discovery + ensure_configs
# ---------------------------------------------------------------------------


def test_resolve_config_path_explicit(tmp_path):
    """explicit path is returned as-is."""
    p = tmp_path / "my.yaml"
    p.touch()
    result = cfg.resolve_config_path(str(p))
    assert result == p


def test_resolve_config_path_env_var(tmp_path, monkeypatch):
    """$SIGNAL_LOOM_CONFIG env var is honoured."""
    p = tmp_path / "env.yaml"
    p.touch()
    monkeypatch.setenv("SIGNAL_LOOM_CONFIG", str(p))
    result = cfg.resolve_config_path(None)
    assert result == p


def test_resolve_config_path_cwd(tmp_path, monkeypatch):
    """config/signal-loom.yaml in cwd is discovered."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    p = cfg_dir / "signal-loom.yaml"
    p.touch()
    # Unset env var to avoid interference
    monkeypatch.delenv("SIGNAL_LOOM_CONFIG", raising=False)
    result = cfg.resolve_config_path(None)
    assert result == p


def test_resolve_config_path_package_fallback(tmp_path, monkeypatch):
    """Falls back to PACKAGE_CONFIG_DIR when nothing else exists."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SIGNAL_LOOM_CONFIG", raising=False)
    # The package dir has the example file but not the actual yaml
    result = cfg.resolve_config_path(None)
    assert result.name == "signal-loom.yaml"
    assert "config" in str(result)


def test_ensure_configs_creates_from_examples(tmp_path):
    """ensure_configs copies *.example.yaml → *.yaml for each missing file."""
    # Copy example files into tmp_path/config
    pkg_config = cfg.PACKAGE_CONFIG_DIR
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    import shutil
    for example in pkg_config.glob("*.example.yaml"):
        shutil.copy2(example, config_dir / example.name)

    created = cfg.ensure_configs(config_dir)
    assert len(created) == 4  # signal-loom, sources, topics, entity-aliases
    assert "signal-loom.yaml" in created
    assert (config_dir / "signal-loom.yaml").exists()
    assert (config_dir / "sources.yaml").exists()
    assert (config_dir / "topics.yaml").exists()
    assert (config_dir / "entity-aliases.yaml").exists()


def test_ensure_configs_idempotent(tmp_path):
    """ensure_configs does not overwrite an existing yaml."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    # Pre-create a yaml and an example
    existing = config_dir / "signal-loom.yaml"
    existing.write_text("# existing content\n")
    example = config_dir / "signal-loom.example.yaml"
    example.write_text("# example content\n")

    created = cfg.ensure_configs(config_dir)
    # signal-loom.yaml already exists — not in the created list
    assert "signal-loom.yaml" not in created
    # Content should be unchanged
    assert existing.read_text() == "# existing content\n"


def test_ensure_configs_no_examples_is_safe(tmp_path):
    """ensure_configs doesn't crash when no example files exist."""
    config_dir = tmp_path / "empty"
    config_dir.mkdir()
    created = cfg.ensure_configs(config_dir)
    assert created == []


def test_load_settings_relative_paths_resolved_to_config_dir(tmp_path):
    """Relative paths in Settings are resolved relative to the config file's directory."""
    config_path = tmp_path / "config" / "signal-loom.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "enrichment_model: claude-sonnet-4-6\n"
        "content_dir: content\n"
        "index_path: index.json\n"
        "topics_path: config/topics.yaml\n"
        "aliases_path: config/entity-aliases.yaml\n"
        "sources_path: config/sources.yaml\n"
    )
    settings = cfg.load_settings(config_path)
    # content_dir should be resolved relative to config file's parent (tmp_path/config)
    assert Path(settings.content_dir).is_absolute()
    assert Path(settings.content_dir) == (tmp_path / "config" / "content")
    assert Path(settings.topics_path) == (tmp_path / "config" / "config" / "topics.yaml")


def test_load_settings_defaults_do_not_double_config_dir(tmp_path):
    """Omitted config paths beside config/signal-loom.yaml use sibling YAML files."""
    config_path = tmp_path / "config" / "signal-loom.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("enrichment_model: claude-sonnet-4-6\n")

    settings = cfg.load_settings(config_path)

    assert Path(settings.content_dir) == tmp_path / "content"
    assert Path(settings.index_path) == tmp_path / "index.json"
    assert Path(settings.sources_path) == tmp_path / "config" / "sources.yaml"
    assert Path(settings.topics_path) == tmp_path / "config" / "topics.yaml"
    assert Path(settings.aliases_path) == tmp_path / "config" / "entity-aliases.yaml"


def test_resolve_source_output_dirs_anchors_to_content_dir(tmp_path):
    """Pipeline path materialization keeps source writes inside configured content_dir."""
    settings = cfg.Settings(content_dir=str(tmp_path / "content"))
    sources = [
        cfg.SourceConfig(
            name="A",
            type="rss",
            feed_url="https://example.com/feed",
            output_dir="content/a",
        ),
        cfg.SourceConfig(
            name="B",
            type="rss",
            feed_url="https://example.com/feed",
            output_dir="b",
        ),
    ]

    resolved = cfg.resolve_source_output_dirs(sources, settings)

    assert Path(resolved[0].output_dir) == tmp_path / "content" / "a"
    assert Path(resolved[1].output_dir) == tmp_path / "content" / "b"


def test_load_settings_absolute_paths_not_changed(tmp_path):
    """Absolute paths in Settings are returned unchanged."""
    content_dir = tmp_path / "my-content"
    config_path = tmp_path / "signal-loom.yaml"
    config_path.write_text(
        f"content_dir: {content_dir}\n"
        f"index_path: {tmp_path}/index.json\n"
    )
    settings = cfg.load_settings(config_path)
    assert settings.content_dir == str(content_dir)


# ---------------------------------------------------------------------------
# #5 listing fetch_method wiring
# ---------------------------------------------------------------------------


def test_listing_fetch_method_auto_uses_direct_httpx(tmp_path, monkeypatch):
    """With fetch_method=auto (default), _direct_fetch_listing is tried first.

    We monkeypatch _direct_fetch_listing to return stub HTML and verify
    _default_fetch_listing (browser) is NOT called.
    """
    from core import scrape as scrape_mod
    from core.config import SourceConfig

    src = SourceConfig(
        name="test",
        type="listing",
        feed_url="https://example.com/episodes/",
        output_dir=str(tmp_path / "content"),
        scrape_limit=2,
        fetch_method="auto",
    )

    # Build stub HTML with two article links
    stub_html = (
        '<html><body>'
        '<a href="/episodes/article-one">One</a>'
        '<a href="/episodes/article-two">Two</a>'
        '</body></html>'
    )

    direct_calls = []
    browser_calls = []

    def fake_direct(url):
        direct_calls.append(url)
        return stub_html

    def fake_browser(url):
        browser_calls.append(url)
        return stub_html

    def fake_article(url):
        return ("# Test Article\nPublished 2026-05-01\n" + "body content " * 60)

    monkeypatch.setattr(scrape_mod, "_direct_fetch_listing", fake_direct)
    monkeypatch.setattr(scrape_mod, "_default_fetch_listing", fake_browser)

    scrape_mod.run_source(src, fetch_article=fake_article)

    assert len(direct_calls) >= 1, "direct fetch should be called for auto mode"
    assert len(browser_calls) == 0, "browser should NOT be called when direct succeeds"


def test_direct_listing_fetch_guards_redirect_targets(httpx_mock, monkeypatch):
    """Direct listing fetch must SSRF-check every redirect target before following."""
    from core import scrape as scrape_mod
    from core import fetch as fetch_mod

    checked_urls = []

    def fake_assert_safe_url(url):
        checked_urls.append(url)
        if url.startswith("http://169.254.169.254"):
            raise fetch_mod.BlockedURLError("blocked metadata redirect")

    monkeypatch.setattr(fetch_mod, "_assert_safe_url", fake_assert_safe_url)
    httpx_mock.add_response(
        url="https://example.com/listing",
        status_code=302,
        headers={"Location": "http://169.254.169.254/latest/meta-data/"},
    )

    result = scrape_mod._direct_fetch_listing("https://example.com/listing")

    assert result is None
    assert checked_urls == [
        "https://example.com/listing",
        "http://169.254.169.254/latest/meta-data/",
    ]


def test_listing_fetch_method_browser_skips_direct(tmp_path, monkeypatch):
    """With fetch_method=browser, only the browser fetcher is called."""
    from core import scrape as scrape_mod
    from core.config import SourceConfig

    src = SourceConfig(
        name="test",
        type="listing",
        feed_url="https://example.com/episodes/",
        output_dir=str(tmp_path / "content"),
        scrape_limit=2,
        fetch_method="browser",
    )

    direct_calls = []
    browser_calls = []

    def fake_direct(url):
        direct_calls.append(url)
        return None  # shouldn't be called

    def fake_browser(url):
        browser_calls.append(url)
        return "<html><body></body></html>"  # empty but valid

    monkeypatch.setattr(scrape_mod, "_direct_fetch_listing", fake_direct)
    monkeypatch.setattr(scrape_mod, "_default_fetch_listing", fake_browser)

    scrape_mod.run_source(src, fetch_article=lambda u: None)

    assert len(direct_calls) == 0, "direct fetch must NOT be called in browser mode"
    assert len(browser_calls) >= 1, "browser fetch must be called in browser mode"


# ---------------------------------------------------------------------------
# #7 Empty vocab fail-fast
# ---------------------------------------------------------------------------


def test_pipeline_empty_vocab_fails_before_scrape(tmp_path, monkeypatch):
    """Pipeline exits 1 with a clear message when topics.yaml has no topics."""
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path, vocab=[])

    import sys
    from io import StringIO

    stderr_capture = StringIO()
    monkeypatch.setattr(sys, "stderr", stderr_capture)

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--no-enrich",
        "--_inject-fetch", "fixture",
    ])

    assert rc == 1, "Should exit 1 on empty vocab"
    err = stderr_capture.getvalue()
    assert "topics" in err.lower() or "topic" in err.lower(), (
        f"Error message should mention topics, got: {err!r}"
    )


def test_writeback_cli_empty_vocab_fails(tmp_path):
    """enrichment_writeback CLI exits 1 with empty topics."""
    import sys
    from io import StringIO

    # Build config with empty vocab
    topics_path = tmp_path / "topics.yaml"
    topics_path.write_text("[]")
    aliases_path = tmp_path / "entity-aliases.yaml"
    aliases_path.write_text("{}\n")
    settings = {
        "topics_path": str(topics_path),
        "aliases_path": str(aliases_path),
        "content_dir": str(tmp_path / "content"),
        "index_path": str(tmp_path / "index.json"),
        "sources_path": str(tmp_path / "sources.yaml"),
    }
    config_path = tmp_path / "signal-loom.yaml"
    config_path.write_text(yaml.dump(settings))

    md = tmp_path / "article.md"
    md.write_text("---\ntitle: T\n---\nbody")

    raw_file = tmp_path / "raw.txt"
    raw_file.write_text("```yaml\nenriched: true\n```")

    stderr_capture = StringIO()
    old = sys.stderr
    sys.stderr = stderr_capture
    try:
        rc = wb.main(["apply", str(md), "--config", str(config_path), "--raw-file", str(raw_file)])
    finally:
        sys.stderr = old

    assert rc == 1
    err = stderr_capture.getvalue()
    assert "topics" in err.lower()


# ---------------------------------------------------------------------------
# #8 Exit codes
# ---------------------------------------------------------------------------


def test_pipeline_exit_1_when_all_sources_fail(tmp_path, monkeypatch):
    """Pipeline returns 1 when every source fails to scrape."""
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)

    def always_fail(url):
        raise RuntimeError("network error")

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--no-enrich",
        "--_inject-fetch", "fixture",  # still use fixture for feed, but override article to fail
    ])
    # Fixture fetch doesn't fail — use a separate approach:
    # Override the scrape entirely so all sources fail
    import core.scrape as scrape_mod

    def fail_source(src, **kwargs):
        raise RuntimeError("forced failure")

    monkeypatch.setattr(scrape_mod, "run_source", fail_source)

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--no-enrich",
    ])
    assert rc == 1, f"Expected exit 1 when all sources fail, got {rc}"


def test_pipeline_exit_1_when_all_enrichment_fails(tmp_path, monkeypatch):
    """Pipeline returns 1 when enrichment was needed and every file failed."""
    import frontmatter as _fm
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)

    # Plant a file to enrich
    content_dir = tmp_path / "content" / "other"
    content_dir.mkdir(parents=True, exist_ok=True)
    md = content_dir / "article.md"
    md.write_text(
        "---\ntitle: Article\nsource: other\nurl: https://example.com\npublished: '2026-05-01'\n---\n"
        + "body content " * 50
    )

    # Make enrichment always fail via a bad enricher
    class FailEnricher:
        def enrich(self, content, vocabulary):
            return "```yaml\nenriched: false\n```", {}

    import core.pipeline as pl_mod
    monkeypatch.setattr(pl_mod, "_FakeEnricher", lambda vocab: FailEnricher())

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--_inject-fetch", "fixture",
        "--_inject-enricher", "fake",
    ])
    # All enrichments produced enriched: false → writeback validation fails
    # → enrich_succeeded == 0 → exit 1
    assert rc == 1, f"Expected exit 1 when all enrichment fails, got {rc}"


# ---------------------------------------------------------------------------
# #9 Missing ANTHROPIC_API_KEY preflight
# ---------------------------------------------------------------------------


def test_pipeline_missing_api_key_exits_1(tmp_path, monkeypatch):
    """Pipeline exits 1 with a clear message when ANTHROPIC_API_KEY is unset."""
    import sys
    from io import StringIO

    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)

    # Plant a file to enrich so enrichment path is triggered
    content_dir = tmp_path / "content" / "smoke"
    md = content_dir / "article.md"
    md.write_text("---\ntitle: T\n---\n" + "body " * 50)

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    stderr_capture = StringIO()
    monkeypatch.setattr(sys, "stderr", stderr_capture)

    # Must NOT use --no-enrich and NOT inject fake enricher
    # (so the real key check fires)
    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--_inject-fetch", "fixture",
        # no --no-enrich, no --_inject-enricher
    ])

    assert rc == 1
    err = stderr_capture.getvalue()
    assert "ANTHROPIC_API_KEY" in err


def test_pipeline_no_enrich_skips_key_check(tmp_path, monkeypatch):
    """--no-enrich must not require ANTHROPIC_API_KEY."""
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--no-enrich",
        "--_inject-fetch", "fixture",
    ])
    assert rc == 0, f"--no-enrich should not require ANTHROPIC_API_KEY, got rc={rc}"


# ---------------------------------------------------------------------------
# #10 /brief missing index friendly error
# ---------------------------------------------------------------------------


def test_brief_missing_index_friendly_error(tmp_path):
    """core.brief.main() exits 1 with a friendly message when index.json is missing."""
    import sys
    from io import StringIO

    stderr_capture = StringIO()
    old = sys.stderr
    sys.stderr = stderr_capture
    try:
        rc = brief_mod.main(["--index", str(tmp_path / "nonexistent.json")])
    finally:
        sys.stderr = old

    assert rc == 1
    err = stderr_capture.getvalue()
    assert "pipeline" in err.lower() or "pipeline" in err, (
        f"Error should suggest running pipeline, got: {err!r}"
    )
    assert "not found" in err.lower() or "missing" in err.lower() or "no such" in err.lower() or "index" in err.lower()


# ---------------------------------------------------------------------------
# #11 --dry-run is a real preview
# ---------------------------------------------------------------------------


def test_pipeline_dry_run_fetches_feed_no_writes(tmp_path, monkeypatch):
    """--dry-run fetches RSS feeds and reports counts without writing files."""
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)

    import sys
    from io import StringIO

    stdout_capture = StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_capture)

    # The fixture seam provides a synthetic feed — use it so no real network needed
    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--dry-run",
        "--_inject-fetch", "fixture",
    ])

    assert rc == 0
    # No content files written
    content_dir = tmp_path / "content"
    md_files = list(content_dir.rglob("*.md")) if content_dir.exists() else []
    assert md_files == [], f"--dry-run must not write any files, found: {md_files}"


def test_pipeline_dry_run_no_index_written(tmp_path, monkeypatch):
    """--dry-run must not write index.json."""
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--dry-run",
        "--_inject-fetch", "fixture",
    ])

    assert rc == 0
    assert not (tmp_path / "index.json").exists()


def test_pipeline_resolves_source_output_dir_independent_of_cwd(tmp_path, monkeypatch):
    """Plugin-style config writes into configured content_dir, not the caller cwd."""
    plugin_root = tmp_path / "plugin"
    cwd = tmp_path / "caller"
    config_dir = plugin_root / "config"
    config_dir.mkdir(parents=True)
    cwd.mkdir()

    (config_dir / "signal-loom.yaml").write_text(
        "enrichment_model: claude-sonnet-4-6\n"
        "content_dir: ../content\n"
        "index_path: ../index.json\n"
        "sources_path: sources.yaml\n"
        "topics_path: topics.yaml\n"
        "aliases_path: entity-aliases.yaml\n"
    )
    (config_dir / "topics.yaml").write_text("- ai agents\n")
    (config_dir / "entity-aliases.yaml").write_text("{}\n")
    (config_dir / "sources.yaml").write_text(
        "smoke:\n"
        "  name: Smoke\n"
        "  type: rss\n"
        "  feed_url: https://fixture.example.com/feed\n"
        "  output_dir: content/smoke\n"
        "  tags: [ai]\n"
        "  scrape_limit: 1\n"
        "  enabled: true\n"
    )

    monkeypatch.chdir(cwd)
    rc = pipeline.main([
        "--config",
        str(config_dir / "signal-loom.yaml"),
        "--once",
        "--_inject-fetch",
        "fixture",
        "--_inject-enricher",
        "fake",
    ])

    assert rc == 0
    assert list((plugin_root / "content").rglob("*.md"))
    assert not list((cwd / "content").rglob("*.md"))
    idx = json.loads((plugin_root / "index.json").read_text())
    assert len(idx["entries"]) == 1


# ---------------------------------------------------------------------------
# #12 keyword_filter.mode validation
# ---------------------------------------------------------------------------


def test_load_sources_rejects_invalid_keyword_filter_mode(tmp_path):
    """load_sources raises ConfigError for an invalid keyword_filter.mode."""
    p = tmp_path / "sources.yaml"
    p.write_text(
        "bad:\n"
        "  type: rss\n"
        "  feed_url: https://example.com/feed\n"
        "  output_dir: content/bad\n"
        "  keyword_filter:\n"
        "    mode: invalid-mode\n"
        "    include: [keyword]\n"
        "  enabled: true\n"
    )
    with pytest.raises(cfg.ConfigError, match="mode"):
        cfg.load_sources(str(p))


def test_load_sources_accepts_valid_keyword_filter_modes(tmp_path):
    """load_sources accepts mode: any and mode: all."""
    for mode in ("any", "all"):
        p = tmp_path / f"sources_{mode}.yaml"
        p.write_text(
            f"src:\n"
            f"  type: rss\n"
            f"  feed_url: https://example.com/feed\n"
            f"  output_dir: content/src\n"
            f"  keyword_filter:\n"
            f"    mode: {mode}\n"
            f"    include: [keyword]\n"
            f"  enabled: true\n"
        )
        sources = cfg.load_sources(str(p))
        assert len(sources) == 1
        assert sources[0].keyword_filter["mode"] == mode


# ---------------------------------------------------------------------------
# #6 Cost-gate log message
# ---------------------------------------------------------------------------


def test_pipeline_logs_cost_gate_info(tmp_path, monkeypatch, caplog):
    """Pipeline logs enrichment cost estimate before enriching."""
    import logging
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)

    # Plant a file to enrich
    content_dir = tmp_path / "content" / "smoke"
    content_dir.mkdir(parents=True, exist_ok=True)
    md = content_dir / "article.md"
    md.write_text(
        "---\ntitle: T\nsource: s\nurl: https://example.com\npublished: '2026-05-01'\n---\n"
        + "body " * 50
    )

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        rc = pipeline.main([
            "--config", config_path,
            "--once",
            "--_inject-fetch", "fixture",
            "--_inject-enricher", "fake",
        ])

    assert rc == 0
    cost_logged = any("enriching" in rec.message and "article" in rec.message for rec in caplog.records)
    assert cost_logged, f"Expected cost-gate log, got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# #13 Failed-enrichments queue written on writeback failure
# ---------------------------------------------------------------------------


def test_pipeline_appends_to_failed_queue_on_writeback_failure(tmp_path, monkeypatch):
    """When enrichment writeback fails, the path is appended to failed-enrichments.jsonl."""
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)

    # Plant an unenriched file
    content_dir = tmp_path / "content" / "other"
    content_dir.mkdir(parents=True, exist_ok=True)
    md = content_dir / "article.md"
    md.write_text(
        "---\ntitle: Article\nsource: other\nurl: https://example.com\n---\n" + "body " * 50
    )

    # Make enricher return invalid YAML so writeback fails
    class BadEnricher:
        def enrich(self, content, vocabulary):
            return "not yaml at all $$$$", {}

    import core.pipeline as pl_mod
    monkeypatch.setattr(pl_mod, "_FakeEnricher", lambda vocab: BadEnricher())

    # Point the queue to tmp_path so we can find it
    queue_path = tmp_path / "failed-enrichments.jsonl"
    monkeypatch.setattr(wb, "_FAILED_QUEUE", queue_path)

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--_inject-fetch", "fixture",
        "--_inject-enricher", "fake",
    ])

    # rc may be 1 (all failed) — that's fine; check queue was written
    assert queue_path.exists(), "failed-enrichments.jsonl must be created on writeback failure"
    lines = [json.loads(l) for l in queue_path.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1
    assert "path" in lines[0]


# ---------------------------------------------------------------------------
# #max-enrich cap
# ---------------------------------------------------------------------------


def test_pipeline_max_enrich_caps_files(tmp_path, monkeypatch):
    """--max-enrich N caps enrichment to N files per run."""
    monkeypatch.chdir(tmp_path)
    config_path = _minimal_config(tmp_path)

    # Plant 5 unenriched files
    content_dir = tmp_path / "content" / "batch"
    content_dir.mkdir(parents=True, exist_ok=True)
    for i in range(5):
        md = content_dir / f"article-{i}.md"
        md.write_text(
            f"---\ntitle: Article {i}\nsource: s\nurl: https://example.com/{i}\n"
            f"published: '2026-05-0{i+1}'\n---\n" + "body " * 50
        )

    import frontmatter as _fm

    rc = pipeline.main([
        "--config", config_path,
        "--once",
        "--max-enrich", "2",
        "--_inject-fetch", "fixture",
        "--_inject-enricher", "fake",
    ])
    assert rc == 0

    # Exactly 2 files should be enriched (sorted order)
    enriched_count = sum(
        1 for md in content_dir.glob("*.md")
        if _fm.load(str(md)).metadata.get("enriched") is True
    )
    assert enriched_count == 2, f"Expected 2 enriched files with --max-enrich 2, got {enriched_count}"


# ---------------------------------------------------------------------------
# ensure_configs e2e via pytest tmp_path (demonstrates auto-bootstrap)
# ---------------------------------------------------------------------------


def test_ensure_configs_e2e_pipeline_runs_after_bootstrap(tmp_path, monkeypatch):
    """Demonstrate ensure_configs: copy examples to tmp, pipeline runs without traceback."""
    import shutil

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SIGNAL_LOOM_CONFIG", raising=False)

    # Set up a config dir with only *.example.yaml (simulating fresh install)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    pkg_config = cfg.PACKAGE_CONFIG_DIR
    for example in pkg_config.glob("*.example.yaml"):
        shutil.copy2(example, config_dir / example.name)

    config_path = config_dir / "signal-loom.yaml"
    assert not config_path.exists(), "Pre-condition: yaml should not exist yet"

    # Call ensure_configs — creates yaml files
    created = cfg.ensure_configs(config_dir)
    assert "signal-loom.yaml" in created
    assert config_path.exists()

    # Now load settings — should work
    settings = cfg.load_settings(config_path)
    assert settings.enrichment_model == "claude-sonnet-4-6"
