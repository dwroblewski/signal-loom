from core import normalize


def test_casefold_dedupes_and_aliases():
    out = normalize.entities(["Anthropic PBC", "anthropic", "OpenAI"], aliases={"anthropic pbc": "Anthropic"})
    assert sorted(out) == ["Anthropic", "OpenAI"]


def test_empty_aliases_still_dedupes():
    out = normalize.entities(["OpenAI", "openai", "OPENAI"], aliases={})
    assert len(out) == 1
