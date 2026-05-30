---
name: brief
description: Render a signal-loom brief from Codex after the index exists. Use on "$brief", "morning brief", digest requests, and recent signal summaries.
---

# brief

Render the grouped markdown digest from the signal-loom index.

## Steps

1. Resolve `ROOT` as described in `$enrich`.
2. Resolve `CONFIG` as described in `$enrich`. Use a user-supplied config path
   or `SIGNAL_LOOM_CONFIG` when present; otherwise default to
   `$ROOT/config/signal-loom.yaml`.

3. Run the brief:
   ```bash
   ROOT="$ROOT" CONFIG="$CONFIG" env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY /bin/sh -c 'uv run --project "$ROOT" python -m core.brief \
       --config "$CONFIG" \
       --since 7d'
   ```

4. For shareable or archived briefs, offer link verification:
   ```bash
   ROOT="$ROOT" CONFIG="$CONFIG" env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY /bin/sh -c 'uv run --project "$ROOT" python -m core.brief \
       --config "$CONFIG" \
       --since 7d \
       --verify'
   ```

## Rules

- This is read-only unless the user explicitly asks to save a brief.
- Warn that `--verify` sends HEAD requests to each unique URL.
