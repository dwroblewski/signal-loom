"""ApiEnricher — headless Anthropic Messages API call site.

Responsibilities:
- Build the enrichment prompt via ``core.prompts``.
- Send the request with the STABLE spec+vocabulary as a cached ``system`` block
  (ephemeral cache_control) and the VOLATILE article as the user message after
  the cache breakpoint. Prompt caching is a prefix match, so the article must
  never sit inside the cached prefix or the cache misses on every call.
- Capture all token-usage buckets (including cache_read / cache_creation) and
  log them via ``core.telemetry``.
- Return the raw text response and the usage dict to the caller.

Explicitly NOT here: YAML validation, normalisation, or file writes.
Those live in ``core.enrichment_writeback``.
"""
from __future__ import annotations

from core import prompts, telemetry

# ---------------------------------------------------------------------------
# Injectable seam — monkeypatched by tests so no real API key is required.
# ---------------------------------------------------------------------------

def _client_create(**kwargs):
    """Thin wrapper around ``anthropic.Anthropic().messages.create``.

    Lazily instantiates the Anthropic client so import of this module never
    triggers credential resolution.  Tests monkeypatch this function to return
    a fake response object without touching the real SDK.
    """
    import anthropic  # local import — avoids hard dep at module load time

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    return client.messages.create(**kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ApiEnricher:
    """Calls the Anthropic Messages API to enrich a single article body.

    Args:
        model: Anthropic model ID (e.g. ``"claude-sonnet-4-6"``).
        max_tokens: Maximum tokens in the model response (default 1500).
    """

    def __init__(self, model: str, max_tokens: int = 1500, cache_system: bool = False) -> None:
        self.model = model
        self.max_tokens = max_tokens
        # Prompt caching is OFF by default. Measured 2026-05-27: the spec+vocab
        # prefix (~1.5K tokens) is below Sonnet's 2048-token cache minimum, so
        # cache_read never fires and the 1.25x write premium makes it a net
        # LOSS. Enable only when your spec/vocab prefix exceeds the model's
        # minimum (Sonnet 2048, Haiku/Opus 4096) — verify with
        # ``client.messages.count_tokens`` that the prefix clears the floor.
        # The real cost lever for the headless batch is the Batch API (50% off,
        # no prefix threshold) — see automation notes.
        self.cache_system = cache_system

    def enrich(self, content: str, vocabulary: set[str]) -> tuple[str, dict]:
        """Run enrichment inference and return the raw response + usage dict.

        The stable spec+vocabulary prefix is sent as a ``system`` block marked
        ``cache_control: {"type": "ephemeral"}``; the per-article body is the
        user message, placed AFTER the cache breakpoint. On a batch of articles
        the spec is written to cache once and read (~0.1x cost) on every
        subsequent call within the 5-minute TTL.

        Note: prompt caching only triggers when the cached prefix exceeds the
        model's minimum (~2048 tokens on Sonnet, ~4096 on Haiku/Opus). Below
        that it silently won't cache (``cache_creation_input_tokens`` stays 0) —
        no error, just no savings.

        Args:
            content: Raw article body text.
            vocabulary: Set of allowed primary topic strings used by the prompt
                builder to constrain tag selection.

        Returns:
            A ``(raw_text, usage)`` tuple where:
            - ``raw_text`` is ``resp.content[0].text`` (the model's raw output).
            - ``usage`` is a dict with keys:
              ``input_tokens``, ``cache_read_input_tokens``,
              ``cache_creation_input_tokens``, ``output_tokens``,
              ``total_input_tokens``.
        """
        system_block: dict = {"type": "text", "text": prompts.system_text(vocabulary)}
        if self.cache_system:
            system_block["cache_control"] = {"type": "ephemeral"}
        system = [system_block]
        messages = [
            {"role": "user", "content": prompts.article_block(content)}
        ]

        resp = _client_create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
        )

        raw: str = resp.content[0].text

        usage: dict = {
            "input_tokens": resp.usage.input_tokens,
            "cache_read_input_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            "output_tokens": resp.usage.output_tokens,
        }
        usage["total_input_tokens"] = (
            usage["input_tokens"]
            + usage["cache_read_input_tokens"]
            + usage["cache_creation_input_tokens"]
        )

        telemetry.log_usage(model=self.model, **usage)

        return raw, usage
