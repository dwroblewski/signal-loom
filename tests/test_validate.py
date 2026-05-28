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
