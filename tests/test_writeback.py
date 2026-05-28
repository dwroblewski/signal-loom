"""Tests for core.enrichment_writeback — the shared enrichment writeback path.

All enrichment output flows through apply() / apply_batch() so validation,
normalization, sanitization, and atomic writes can't drift between callers.
"""

from core import enrichment_writeback as wb
import frontmatter
import pytest

VOCAB = {"ai"}

RAW_OK = (
    "```yaml\n"
    "enriched: true\n"
    "summary: " + "x" * 200 + "\n"
    "topics:\n"
    "  primary: [ai]\n"
    "  secondary: []\n"
    "entities:\n"
    "  organizations: [Acme]\n"
    "  people: []\n"
    "```"
)


def test_parse_validate_normalize_merge(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("---\ntitle: T\ntags: [x]\n---\nbody")
    res = wb.apply(f, RAW_OK, vocabulary=VOCAB, aliases={})
    post = frontmatter.load(str(f))
    assert res.ok and post["enriched"] is True and post["title"] == "T"  # unknown key preserved


def test_malformed_retries_then_skips(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("---\ntitle: T\n---\nbody")
    res = wb.apply(f, "not yaml at all", vocabulary=VOCAB, aliases={}, retries=2)
    assert not res.ok and res.attempts == 3 and f.read_text().startswith("---\ntitle: T")  # untouched


def test_regenerate_retry_succeeds(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("---\ntitle: T\n---\nbody")
    calls = {"n": 0}

    def regen():
        calls["n"] += 1
        return RAW_OK if calls["n"] >= 1 else "garbage"

    res = wb.apply(f, "garbage", vocabulary=VOCAB, aliases={}, retries=2, regenerate=regen)
    assert res.ok and frontmatter.load(str(f))["enriched"] is True


def test_atomic_write_no_partial(tmp_path, monkeypatch):
    f = tmp_path / "a.md"
    f.write_text("---\ntitle: T\n---\nbody")
    monkeypatch.setattr(wb.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        wb.apply(f, RAW_OK, vocabulary=VOCAB, aliases={})
    assert "enriched" not in f.read_text()  # original intact, no temp leak
    assert list(f.parent.glob("*.tmp*")) == [] and list(f.parent.glob("*~")) == []  # no temp file left


def test_partial_batch_skips_one(tmp_path):
    files = [tmp_path / f"{i}.md" for i in range(3)]
    [x.write_text("---\ntitle: T\n---\nb") for x in files]
    report = wb.apply_batch(
        {files[0]: RAW_OK, files[1]: "garbage", files[2]: RAW_OK},
        vocabulary=VOCAB,
        aliases={},
    )
    assert report.succeeded == 2 and report.failed == [files[1]]


def test_injected_key_not_written(tmp_path):
    raw = RAW_OK.replace("enriched: true\n", "enriched: true\nrun_command: rm -rf /\n")
    f = tmp_path / "a.md"
    f.write_text("---\ntitle: T\n---\nbody")
    wb.apply(f, raw, vocabulary=VOCAB, aliases={})
    assert "run_command" not in frontmatter.load(str(f)).metadata  # sanitize boundary holds end-to-end


# ---------------------------------------------------------------------------
# Fix #1 — --raw-file CLI option (no shell interpolation)
# ---------------------------------------------------------------------------


def _make_writeback_config(tmp_path) -> str:
    """Write a minimal signal-loom.yaml + topics.yaml so the CLI can load vocab.

    The vocab includes "ai" to match RAW_OK's topics.primary: [ai].
    Returns the path to signal-loom.yaml as a str.
    """
    import yaml

    topics_path = tmp_path / "topics.yaml"
    topics_path.write_text(yaml.dump(["ai", "ai agents", "model releases"]))

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
    return str(config_path)


def test_main_raw_file_reads_from_file(tmp_path):
    """The CLI's --raw-file option must read raw model output from a file, not stdin.

    This avoids shell interpolation of model output (the injection vector is
    echo "<raw>" | ...). With --raw-file, the content is passed via the
    filesystem rather than shell interpolation.
    """
    import sys
    from io import StringIO

    config = _make_writeback_config(tmp_path)

    # Markdown file to enrich.
    md = tmp_path / "article.md"
    md.write_text("---\ntitle: Article\n---\nbody")

    # Raw output in a temp file (not piped via echo).
    raw_file = tmp_path / "raw_output.txt"
    raw_file.write_text(RAW_OK)

    # Run CLI with --raw-file; stdin is empty (simulates no pipe).
    old_stdin = sys.stdin
    sys.stdin = StringIO("")
    try:
        rc = wb.main(["apply", str(md), "--config", config, "--raw-file", str(raw_file)])
    finally:
        sys.stdin = old_stdin

    assert rc == 0, "CLI must succeed with --raw-file"
    post = frontmatter.load(str(md))
    assert post.metadata.get("enriched") is True, "File must be enriched via --raw-file"


def test_main_stdin_still_works(tmp_path):
    """The CLI must still accept raw model output from stdin when --raw-file is not given."""
    import sys
    from io import StringIO

    config = _make_writeback_config(tmp_path)

    md = tmp_path / "article.md"
    md.write_text("---\ntitle: Article\n---\nbody")

    old_stdin = sys.stdin
    sys.stdin = StringIO(RAW_OK)
    try:
        rc = wb.main(["apply", str(md), "--config", config])
    finally:
        sys.stdin = old_stdin

    assert rc == 0, "CLI must succeed reading from stdin"
    post = frontmatter.load(str(md))
    assert post.metadata.get("enriched") is True
