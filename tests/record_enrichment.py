"""One-time recorder: calls the real LLM via the shipped ApiEnricher and saves
the raw response as a fixture for the offline schema-gate test
(tests/test_enrich_live.py).

Uses ``core.enrich.ApiEnricher`` — the exact code path the headless pipeline
uses — so the fixture faithfully represents what signal-loom actually produces.
Requires ``ANTHROPIC_API_KEY`` in the environment.

Usage:
    cd ~/dev/signal-loom
    uv run python tests/record_enrichment.py
"""

import json
import os
from pathlib import Path


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — export it before recording")

    from core import config, enrich  # local import intentional

    content = (Path(__file__).parent / "fixtures" / "ai_article.txt").read_text()
    vocab = config.load_vocabulary("config/topics.example.yaml")

    # Record with a cheap model; the schema gate only cares about output shape.
    raw, usage = enrich.ApiEnricher(model="claude-haiku-4-5").enrich(content, vocab)

    out = Path(__file__).parent / "fixtures" / "recorded_enrichment.json"
    out.write_text(json.dumps({"text": raw, "usage": usage}, indent=2))
    print(f"recorded {len(raw)} chars via ApiEnricher; usage {usage}")


if __name__ == "__main__":
    main()
