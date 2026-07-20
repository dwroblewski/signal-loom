"""Builds the enrichment prompt from the single canonical spec + runtime inputs.

Two shapes:
- ``build()`` returns one combined string — used by the interactive sub-agent
  path, where the whole prompt is the agent's task text.
- ``system_text()`` / ``article_block()`` split the prompt into a STABLE prefix
  (spec + allowed-topic vocabulary) and the VOLATILE article. The headless API
  path (``core.enrich.ApiEnricher``) caches the stable prefix and sends the
  article after the cache breakpoint — anything volatile in the cached prefix
  would invalidate the cache on every call (prompt caching is a prefix match).
"""
from pathlib import Path

SPEC = (Path(__file__).parent / "enrichment_spec.md").read_text(encoding="utf-8")


def _vocab_line(vocabulary: set[str]) -> str:
    return "Allowed primary topics: " + (
        ", ".join(sorted(vocabulary)) if vocabulary else "(none configured)"
    )


def system_text(vocabulary: set[str]) -> str:
    """Stable, cacheable prefix: spec + allowed-topic vocabulary.

    Constant across every article in a run (the vocabulary comes from config),
    so it is safe to mark with ``cache_control`` — repeated calls reuse it.
    """
    return f"{SPEC}\n\n{_vocab_line(vocabulary)}"


def article_block(content: str, max_chars: int = 50000) -> str:
    """Volatile per-article content — must come AFTER the cache breakpoint."""
    body = content[:max_chars]
    return f"--- ARTICLE START ---\n{body}\n--- ARTICLE END ---\n"


def build(content: str, vocabulary: set[str], max_chars: int = 50000) -> str:
    return f"{system_text(vocabulary)}\n\n{article_block(content, max_chars)}"
