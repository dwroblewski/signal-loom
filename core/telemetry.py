"""Append-only per-call token-usage telemetry (JSONL).

Each call to ``log_usage`` appends a single JSON record to a rotating log file
under ``$SIGNAL_LOOM_STATE`` (defaulting to ``~/.local/state/signal-loom``).
The file captures all token buckets — including ``cache_read_input_tokens`` and
``cache_creation_input_tokens`` — so cost accounting works correctly even when
prompt-caching makes bare ``input_tokens`` appear tiny.
"""
import json
import os
from datetime import datetime
from pathlib import Path

TOKEN_LOG = Path(
    os.environ.get("SIGNAL_LOOM_STATE", Path.home() / ".local/state/signal-loom")
) / "tokens.jsonl"


def log_usage(**fields) -> None:
    """Append a token-usage record to the JSONL log.

    Args:
        **fields: Arbitrary keyword fields to include in the record.
            Expected keys: model, input_tokens, cache_read_input_tokens,
            cache_creation_input_tokens, output_tokens, total_input_tokens.
    """
    TOKEN_LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": datetime.now().isoformat(), **fields}
    with open(TOKEN_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
