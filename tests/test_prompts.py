from core import prompts


def test_prompt_embeds_content_and_vocab():
    p = prompts.build("ARTICLE BODY", vocabulary={"ai", "climate"})
    assert "ARTICLE BODY" in p and "ai" in p and "climate" in p and "YAML" in p


def test_prompt_truncates_long_content():
    assert len(prompts.build("x" * 100000, vocabulary=set())) < 80000
