"""tests/test_query.py — Tests for core.query windowed index reads."""

from core import query


def test_window_by_date(index_file):
    res = query.window(index_file, since="2026-05-21")
    assert len(res) == 1 and res[0]["published"] >= "2026-05-21"


def test_window_by_topic(index_file):
    res = query.window(index_file, topic="model releases")
    assert len(res) == 1 and "model releases" in res[0]["topics"]["primary"]


def test_window_by_source(index_file):
    res = query.window(index_file, source="one-useful-thing")
    assert len(res) == 1 and res[0]["source"] == "one-useful-thing"


def test_window_caps_and_returns_list(index_file):
    res = query.window(index_file, limit=1)
    assert isinstance(res, list) and len(res) == 1


def test_window_sorted_desc(index_file):
    res = query.window(index_file)
    assert [e["published"] for e in res] == sorted(
        [e["published"] for e in res], reverse=True
    )


def test_window_no_matches_returns_empty(index_file):
    assert query.window(index_file, source="nonexistent") == []
