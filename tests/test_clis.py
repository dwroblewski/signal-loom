"""Tests for CLI entrypoints: core.index, core.enrichment_writeback, core.config.

These tests exercise the ``python -m core.<module>`` paths via subprocess so
they catch import-time and argparse failures that unit tests of the library
functions alone would miss.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import frontmatter
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent

# Minimal valid enrichment YAML (matches enrichment_spec schema).
VALID_YAML = (
    "```yaml\n"
    "enriched: true\n"
    "summary: " + "This is a detailed and informative summary of the article. " * 4 + "\n"
    "topics:\n"
    "  primary:\n"
    "    - ai agents\n"
    "  secondary:\n"
    "    - llm tooling\n"
    "entities:\n"
    "  organizations:\n"
    "    - Anthropic\n"
    "  people:\n"
    "    - Dario Amodei\n"
    "key_takeaways:\n"
    "  - AI agents are rapidly evolving.\n"
    "```\n"
)


def _run(module: str, args: list[str], *, stdin: str = "", env: dict | None = None):
    """Run ``python -m <module>`` as a subprocess and return CompletedProcess."""
    import subprocess

    run_env = {**os.environ}
    if env:
        run_env.update(env)

    return subprocess.run(
        [sys.executable, "-m", module, *args],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=run_env,
    )


def _make_config(tmp_path: Path, *, content_dir: Path, index_path: Path) -> Path:
    """Write a minimal signal-loom.yaml into tmp_path and return its path."""
    topics = tmp_path / "topics.yaml"
    topics.write_text("- ai agents\n- model releases\n")

    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("{}\n")

    cfg = tmp_path / "signal-loom.yaml"
    cfg.write_text(
        f"content_dir: {content_dir}\n"
        f"index_path: {index_path}\n"
        f"topics_path: {topics}\n"
        f"aliases_path: {aliases}\n"
    )
    return cfg


# ---------------------------------------------------------------------------
# core.index CLI
# ---------------------------------------------------------------------------


class TestIndexCLI:
    def test_builds_index_and_exits_zero(self, tmp_path):
        """``python -m core.index --config <cfg>`` creates index.json with the entry."""
        # Create a minimal enriched markdown file in a temp content dir.
        content_dir = tmp_path / "content"
        content_dir.mkdir()
        md = content_dir / "article.md"
        md.write_text(
            "---\n"
            "title: Test Article\n"
            "source: test\n"
            "url: https://example.com\n"
            "published: '2026-05-20'\n"
            "tags: [ai]\n"
            "enriched: true\n"
            "summary: A summary.\n"
            "topics:\n"
            "  primary: [ai agents]\n"
            "  secondary: []\n"
            "entities:\n"
            "  organizations: []\n"
            "  people: []\n"
            "key_takeaways: [A point.]\n"
            "---\n"
            "Body text here.\n"
        )

        index_path = tmp_path / "index.json"
        cfg = _make_config(tmp_path, content_dir=content_dir, index_path=index_path)

        result = _run("core.index", ["--config", str(cfg)])

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert index_path.exists(), "index.json was not created"

        data = json.loads(index_path.read_text())
        assert len(data["entries"]) == 1
        assert data["entries"][0]["title"] == "Test Article"
        assert "1 entries" in result.stdout

    def test_skips_unenriched_file(self, tmp_path):
        """Unenriched files are excluded from the index."""
        content_dir = tmp_path / "content"
        content_dir.mkdir()
        (content_dir / "draft.md").write_text("---\ntitle: Draft\n---\nno enrichment\n")

        index_path = tmp_path / "index.json"
        cfg = _make_config(tmp_path, content_dir=content_dir, index_path=index_path)

        result = _run("core.index", ["--config", str(cfg)])

        assert result.returncode == 0
        data = json.loads(index_path.read_text())
        assert len(data["entries"]) == 0


# ---------------------------------------------------------------------------
# core.enrichment_writeback CLI
# ---------------------------------------------------------------------------


class TestEnrichmentWritebackCLI:
    def _make_md(self, tmp_path: Path) -> Path:
        """Create a minimal markdown file for writeback tests."""
        md = tmp_path / "article.md"
        md.write_text("---\ntitle: My Article\ntags: [x]\n---\nBody content.\n")
        return md

    def test_apply_valid_yaml_enriches_file(self, tmp_path):
        """``apply <path>`` with valid YAML on stdin enriches the file (exit 0)."""
        md = self._make_md(tmp_path)
        cfg = _make_config(
            tmp_path,
            content_dir=tmp_path / "content",
            index_path=tmp_path / "index.json",
        )

        result = _run(
            "core.enrichment_writeback",
            ["apply", str(md), "--config", str(cfg)],
            stdin=VALID_YAML,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert f"OK {md}" in result.stdout

        post = frontmatter.load(str(md))
        assert post["enriched"] is True
        assert post["title"] == "My Article"  # pre-existing key preserved

    def test_apply_garbage_stdin_exits_nonzero_file_untouched(self, tmp_path):
        """``apply <path>`` with garbage on stdin exits 1 and leaves file unchanged."""
        md = self._make_md(tmp_path)
        original_content = md.read_text()
        cfg = _make_config(
            tmp_path,
            content_dir=tmp_path / "content",
            index_path=tmp_path / "index.json",
        )

        result = _run(
            "core.enrichment_writeback",
            ["apply", str(md), "--config", str(cfg)],
            stdin="this is definitely not valid yaml enrichment output !!!",
        )

        assert result.returncode == 1
        assert md.read_text() == original_content  # file untouched

    def test_no_subcommand_exits_nonzero(self, tmp_path):
        """Calling without subcommand prints help and exits non-zero."""
        result = _run("core.enrichment_writeback", [])
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# core.config CLI
# ---------------------------------------------------------------------------


class TestConfigCLI:
    def test_print_content_dir(self, tmp_path):
        """``--print content_dir`` prints the configured value."""
        content_dir = tmp_path / "mycontent"
        index_path = tmp_path / "myindex.json"
        cfg = _make_config(tmp_path, content_dir=content_dir, index_path=index_path)

        result = _run("core.config", ["--print", "content_dir", "--config", str(cfg)])

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert str(content_dir) in result.stdout.strip()

    def test_print_index_path(self, tmp_path):
        """``--print index_path`` prints the configured index path."""
        content_dir = tmp_path / "content"
        index_path = tmp_path / "custom-index.json"
        cfg = _make_config(tmp_path, content_dir=content_dir, index_path=index_path)

        result = _run("core.config", ["--print", "index_path", "--config", str(cfg)])

        assert result.returncode == 0
        assert str(index_path) in result.stdout.strip()

    def test_unknown_field_exits_nonzero(self, tmp_path):
        """``--print nonexistent_field`` exits 1."""
        content_dir = tmp_path / "content"
        cfg = _make_config(
            tmp_path,
            content_dir=content_dir,
            index_path=tmp_path / "index.json",
        )

        result = _run("core.config", ["--print", "nonexistent_field", "--config", str(cfg)])

        assert result.returncode == 1
        assert "unknown field" in result.stderr

    def test_missing_config_exits_nonzero(self, tmp_path):
        """Pointing at a nonexistent config file exits 1."""
        result = _run(
            "core.config",
            ["--print", "content_dir", "--config", str(tmp_path / "no-such.yaml")],
        )
        assert result.returncode == 1
