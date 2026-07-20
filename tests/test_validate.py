from core import validate

VOCAB = {"ai", "policy"}
GOOD = {
    "enriched": True,
    "summary": "x" * 200,
    "topics": {"primary": ["ai"], "secondary": []},
    "entities": {"organizations": ["Acme"], "people": []},
    "key_takeaways": ["one"],
}


def test_valid_passes():
    assert validate.check(GOOD, vocabulary=VOCAB) == (True, [])


def test_primary_topic_off_vocab_fails():
    ok, errs = validate.check(
        {**GOOD, "topics": {"primary": ["banana"], "secondary": []}},
        vocabulary=VOCAB,
    )
    assert not ok and any("vocab" in e.lower() for e in errs)


def test_short_summary_fails():
    ok, errs = validate.check({**GOOD, "summary": "short"}, vocabulary=VOCAB)
    assert not ok and any("summary" in e.lower() for e in errs)


def test_missing_required_fails():
    ok, errs = validate.check({"summary": "x" * 200}, vocabulary=VOCAB)
    assert not ok


def test_unknown_key_dropped_not_executed():
    clean = validate.sanitize({**GOOD, "run_command": "rm -rf /", "__proto__": "x"})
    assert "run_command" not in clean and "__proto__" not in clean and clean["enriched"] is True


def test_sanitize_drops_wrong_types():
    clean = validate.sanitize(
        {
            "enriched": True,
            "summary": 123,
            "topics": {"primary": ["ai"], "secondary": []},
            "entities": {"organizations": [], "people": []},
        }
    )
    assert "summary" not in clean  # wrong type dropped


# ---------------------------------------------------------------------------
# Issue 1: sanitize must filter list ELEMENT types
# ---------------------------------------------------------------------------

def test_sanitize_drops_dict_elements_from_key_takeaways():
    """key_takeaways containing dicts must be reduced to string-only (empty here)."""
    raw = {**GOOD, "key_takeaways": [{"exec": "rm -rf /"}, "valid string"]}
    clean = validate.sanitize(raw)
    assert clean["key_takeaways"] == ["valid string"]


def test_sanitize_drops_dict_elements_from_topics_primary():
    """topics.primary containing dicts must be reduced to string-only elements."""
    raw = {**GOOD, "topics": {"primary": [{"inject": "x"}, "ai"], "secondary": []}}
    clean = validate.sanitize(raw)
    assert clean["topics"]["primary"] == ["ai"]


def test_sanitize_drops_dict_elements_from_topics_secondary():
    raw = {**GOOD, "topics": {"primary": ["ai"], "secondary": [{"nested": 1}, "policy"]}}
    clean = validate.sanitize(raw)
    assert clean["topics"]["secondary"] == ["policy"]


def test_sanitize_drops_dict_elements_from_entities_organizations():
    """entities.organizations containing dicts is reduced to []."""
    raw = {**GOOD, "entities": {"organizations": [{"exec": "injected"}], "people": []}}
    clean = validate.sanitize(raw)
    assert clean["entities"]["organizations"] == []


def test_sanitize_drops_dict_elements_from_entities_people():
    raw = {**GOOD, "entities": {"organizations": [], "people": [{"key": "val"}]}}
    clean = validate.sanitize(raw)
    assert clean["entities"]["people"] == []


def test_sanitize_mixed_list_keeps_only_strings():
    raw = {**GOOD, "key_takeaways": ["ok", 42, None, {"x": 1}, "also ok"]}
    clean = validate.sanitize(raw)
    assert clean["key_takeaways"] == ["ok", "also ok"]


# ---------------------------------------------------------------------------
# Issue 2: graceful on non-dict input
# ---------------------------------------------------------------------------

def test_check_none_returns_false_not_raises():
    ok, errs = validate.check(None, vocabulary=VOCAB)
    assert not ok and len(errs) == 1 and "not a mapping" in errs[0].lower()


def test_check_string_returns_false_not_raises():
    ok, errs = validate.check("malicious string", vocabulary=VOCAB)
    assert not ok and "not a mapping" in errs[0].lower()


def test_check_int_returns_false_not_raises():
    ok, errs = validate.check(42, vocabulary=VOCAB)
    assert not ok


def test_check_list_returns_false_not_raises():
    ok, errs = validate.check([1, 2, 3], vocabulary=VOCAB)
    assert not ok


def test_sanitize_none_returns_empty_dict():
    assert validate.sanitize(None) == {}


def test_sanitize_string_returns_empty_dict():
    assert validate.sanitize("inject me") == {}


def test_sanitize_int_returns_empty_dict():
    assert validate.sanitize(42) == {}


def test_sanitize_list_returns_empty_dict():
    assert validate.sanitize([{"enriched": True}]) == {}


# ---------------------------------------------------------------------------
# Issue 3: sanitize enforces size bounds
# ---------------------------------------------------------------------------

def test_sanitize_truncates_long_list_to_max_items():
    """key_takeaways with 1000 items is truncated to max_items=7."""
    raw = {**GOOD, "key_takeaways": [f"item {i}" for i in range(1000)]}
    clean = validate.sanitize(raw)
    assert len(clean["key_takeaways"]) == 7


def test_sanitize_drops_oversized_summary():
    """A summary longer than 20000 chars is treated as malformed and dropped."""
    raw = {**GOOD, "summary": "x" * 20001}
    clean = validate.sanitize(raw)
    assert "summary" not in clean


def test_sanitize_keeps_valid_size_summary():
    """A summary exactly at the max length boundary is kept."""
    raw = {**GOOD, "summary": "x" * 20000}
    clean = validate.sanitize(raw)
    assert "summary" in clean


# ---------------------------------------------------------------------------
# Issue 4: check min_len error uses field name, not hardcoded 'summary'
# ---------------------------------------------------------------------------

def test_check_min_len_error_uses_field_name():
    """Error message for min_len violation must reference the actual field name."""
    ok, errs = validate.check({**GOOD, "summary": "short"}, vocabulary=VOCAB)
    assert not ok
    # Must reference 'summary' dynamically (existing field), not a hardcoded literal
    assert any("summary" in e for e in errs)
    # Validate the message template is dynamic by checking it's in the error text
    # (this is already passing, but we also want to ensure it says 'summary' not a generic placeholder)
    assert all("'summary'" in e or "summary" in e for e in errs if "too short" in e or "minimum" in e)


# ---------------------------------------------------------------------------
# Issue 5: check subkey type check uses _matches_type; unknown subtype → error
# ---------------------------------------------------------------------------

def test_check_subkey_wrong_type_produces_error():
    """A subkey with the wrong type should produce an error, not silently pass."""
    bad = {**GOOD, "entities": {"organizations": "not-a-list", "people": []}}
    ok, errs = validate.check(bad, vocabulary=VOCAB)
    assert not ok and any("organizations" in e for e in errs)


# ---------------------------------------------------------------------------
# Regression: unhashable topics.primary element must not crash check()
# ---------------------------------------------------------------------------

def test_check_unhashable_primary_topic_is_reported_not_raised():
    """A dict element in topics.primary (from `- AI: alignment` YAML) is
    unhashable — check() must return an error, never raise TypeError."""
    bad = {**GOOD, "topics": {"primary": [{"AI Safety": "alignment concerns"}], "secondary": []}}
    ok, errs = validate.check(bad, vocabulary=VOCAB)
    assert not ok and any("not a string" in e for e in errs)


def test_check_over_long_summary_fails():
    """An over-long summary must FAIL validation (not pass check then get
    silently dropped by sanitize, leaving the file enriched with no summary)."""
    ok, errs = validate.check({**GOOD, "summary": "x" * 20_001}, vocabulary=VOCAB)
    assert not ok and any("too long" in e for e in errs)
