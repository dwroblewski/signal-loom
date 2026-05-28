from core import normalize


def test_casefold_dedupes_and_aliases():
    out = normalize.entities(["Anthropic PBC", "anthropic", "OpenAI"], aliases={"anthropic pbc": "Anthropic"})
    assert sorted(out) == ["Anthropic", "OpenAI"]


def test_empty_aliases_still_dedupes():
    out = normalize.entities(["OpenAI", "openai", "OPENAI"], aliases={})
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Issue 6: normalize.entities guards non-string items
# ---------------------------------------------------------------------------

def test_entities_skips_non_string_items():
    """Non-string items (dict, int, None) must be skipped, not crash."""
    out = normalize.entities(["Anthropic", {"inject": "x"}, None, 42, "OpenAI"], aliases={})
    assert out == ["Anthropic", "OpenAI"]


def test_entities_all_non_strings_returns_empty():
    out = normalize.entities([{"a": 1}, 99, None], aliases={})
    assert out == []


# ---------------------------------------------------------------------------
# Issue 7: normalize_entities_dict only emits known subkeys
# ---------------------------------------------------------------------------

def test_normalize_entities_dict_drops_unknown_subkeys():
    """Unknown subkeys must be dropped, not passed through."""
    d = {
        "organizations": ["Acme"],
        "people": ["Alice"],
        "injected_key": ["malicious"],
        "extra": 99,
    }
    result = normalize.normalize_entities_dict(d, aliases={})
    assert "injected_key" not in result
    assert "extra" not in result
    assert "organizations" in result
    assert "people" in result


def test_normalize_entities_dict_handles_missing_known_subkeys():
    """If a known subkey is absent it should simply be absent in the result."""
    d = {"organizations": ["Acme"]}
    result = normalize.normalize_entities_dict(d, aliases={})
    assert "organizations" in result
    assert "people" not in result


def test_normalize_entities_dict_only_known_subkeys_with_lists():
    """Known subkeys that are not lists should be skipped (not crash)."""
    d = {"organizations": "not-a-list", "people": ["Alice"]}
    result = normalize.normalize_entities_dict(d, aliases={})
    assert "organizations" not in result or result.get("organizations") != "not-a-list"
    assert result["people"] == ["Alice"]
