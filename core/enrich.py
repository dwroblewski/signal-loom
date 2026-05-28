"""ApiEnricher — headless Anthropic Messages API call site.

Responsibilities:
- Build the enrichment prompt via ``core.prompts.build``.
- Send the prompt to the Anthropic Messages API with prompt-caching enabled
  on the user message (ephemeral cache_control).
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

    def __init__(self, model: str, max_tokens: int = 1500) -> None:
        self.model = model
        self.max_tokens = max_tokens

    def enrich(self, content: str, vocabulary: set[str]) -> tuple[str, dict]:
        """Run enrichment inference and return the raw response + usage dict.

        The full prompt is sent as a single user message with ``cache_control``
        set to ``{"type": "ephemeral"}`` so the large spec block is eligible
        for prompt-caching on repeated calls.

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
        prompt = prompts.build(content, vocabulary)

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ]

        resp = _client_create(
            model=self.model,
            max_tokens=self.max_tokens,
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
