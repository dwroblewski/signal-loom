"""tests/test_index.py — Tests for core.index.build_index."""

import json
from pathlib import Path

import pytest

from core import index


def test_build_matches_golden(tmp_path):
    """build_index output must exactly match the committed golden index."""
    src = Path(__file__).parent / "fixtures/golden/content"
    out = tmp_path / "index.json"
    index.build_index(src, out)
    got = json.loads(out.read_text())
    want = json.loads((src.parent / "index.json").read_text())
    assert got["entries"] == want["entries"]


def test_skips_unenriched_files(tmp_path):
    """Files without enriched: true must be excluded from the index."""
    (tmp_path / "raw.md").write_text("---\ntitle: T\n---\nbody")  # no enriched: true
    out = tmp_path / "index.json"
    index.build_index(tmp_path, out)
    assert json.loads(out.read_text())["entries"] == []


# ---------------------------------------------------------------------------
# Fix #8 — _ensure_list type-defensiveness + per-file robustness + atomic write
# ---------------------------------------------------------------------------


def test_ensure_list_int_returns_empty():
    """_ensure_list must return [] for a bare int (non-coercible scalar)."""
    assert index._ensure_list(123) == []


def test_ensure_list_dict_returns_empty():
    """_ensure_list must return [] for a dict (cannot meaningfully list-ify)."""
    assert index._ensure_list({"a": 1}) == []


def test_ensure_list_none_returns_empty():
    """_ensure_list must return [] for None."""
    assert index._ensure_list(None) == []


def test_ensure_list_string_wraps():
    """_ensure_list must wrap a bare string in a single-element list."""
    assert index._ensure_list("ai agents") == ["ai agents"]


def test_ensure_list_list_passthrough():
    """_ensure_list must return an existing list as-is."""
    assert index._ensure_list(["a", "b"]) == ["a", "b"]


def test_index_skips_malformed_file_with_int_tags(tmp_path):
    """A file with tags: 123 (non-list int) must not crash the index build.

    Either the file is skipped (entry omitted) or its tags are coerced to [].
    Either way, other valid files must still be indexed.
    """
    # Malformed file: tags is an int
    (tmp_path / "malformed.md").write_text(
        "---\n"
        "title: Malformed\n"
        "enriched: true\n"
        "tags: 123\n"
        "published: '2026-05-01'\n"
        "---\nbody"
    )
    # Valid file
    (tmp_path / "good.md").write_text(
        "---\n"
        "title: Good Article\n"
        "enriched: true\n"
        "tags: [ai]\n"
        "published: '2026-05-02'\n"
        "---\nbody"
    )

    out = tmp_path / "index.json"
    result = index.build_index(tmp_path, out)

    # Must not crash; good file must be present in entries
    good_titles = [e["title"] for e in result["entries"]]
    assert "Good Article" in good_titles, f"Good file must be indexed; got: {good_titles}"


def test_index_atomic_write_no_partial_on_failure(tmp_path, monkeypatch):
    """If os.replace fails, no partial index.json must be left on disk."""
    import os as _os

    (tmp_path / "article.md").write_text(
        "---\nenriched: true\ntitle: T\n---\nbody"
    )
    out = tmp_path / "index.json"

    # Simulate os.replace failure after the temp file is written.
    _orig_replace = _os.replace

    def _failing_replace(src, dst):
        raise OSError("simulated disk full")

    monkeypatch.setattr(_os, "replace", _failing_replace)

    with pytest.raises(OSError, match="simulated disk full"):
        index.build_index(tmp_path, out)

    # index.json must not exist (write was aborted)
    assert not out.exists(), "Partial index.json must not be left on disk after failed atomic write"
    # No .tmp files left behind either
    assert list(tmp_path.glob("*.tmp*")) == [], "Temp file must be cleaned up after failure"
