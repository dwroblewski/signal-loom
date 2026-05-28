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
