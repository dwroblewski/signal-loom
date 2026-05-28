# Run manually ONCE: uv run python tests/record_enrichment.py
"""One-time recorder: calls the real LLM and saves the raw response fixture.

Uses the LLM provider (anthropic/claude-haiku-4-5) via LLM_API_KEY since
ANTHROPIC_API_KEY on this host is expired.  The full enrichment prompt is
built identically to core.enrich.ApiEnricher (via core.prompts.build) so the
fixture faithfully represents real model output for the schema gate test.

Usage:
    cd ~/dev/signal-loom
    uv run python tests/record_enrichment.py
"""

import json
import os
from pathlib import Path

import httpx


def main() -> None:
    content = (Path(__file__).parent / "fixtures" / "ai_article.txt").read_text()

    from core import config, prompts  # noqa: PLC0415 — local import intentional

    vocab = config.load_vocabulary("config/topics.example.yaml")
    prompt = prompts.build(content, vocab)

    or_key = os.environ.get("LLM_API_KEY", "")
    if not or_key:
        raise RuntimeError("LLM_API_KEY not set — source ~/.shell-profile or set it manually")

    resp = httpx.post(
        "https://example.com/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {or_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://signal-loom",
            "X-Title": "signal-loom",
        },
        json={
            "model": "anthropic/claude-haiku-4-5",
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()

    raw: str = data["choices"][0]["message"]["content"]
    usage_raw = data.get("usage", {})
    usage = {
        "input_tokens": usage_raw.get("prompt_tokens", 0),
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": usage_raw.get("completion_tokens", 0),
        "total_input_tokens": usage_raw.get("prompt_tokens", 0),
    }

    out = Path(__file__).parent / "fixtures" / "recorded_enrichment.json"
    out.write_text(json.dumps({"text": raw, "usage": usage}, indent=2))
    print(f"recorded {len(raw)} chars; usage {usage}")


if __name__ == "__main__":
    main()
