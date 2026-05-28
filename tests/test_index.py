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
