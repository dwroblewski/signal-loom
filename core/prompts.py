"""Builds the enrichment prompt from the single canonical spec + runtime inputs."""
from pathlib import Path

SPEC = (Path(__file__).parent / "enrichment_spec.md").read_text()


def build(content: str, vocabulary: set[str], max_chars: int = 50000) -> str:
    vocab_line = "Allowed primary topics: " + (", ".join(sorted(vocabulary)) if vocabulary else "(none configured)")
    body = content[:max_chars]
    return f"{SPEC}\n\n{vocab_line}\n\n--- ARTICLE START ---\n{body}\n--- ARTICLE END ---\n"
