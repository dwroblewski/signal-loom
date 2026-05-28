---
name: signal-loom-brief
description: Render a signal-loom brief from Codex after the index exists. Use for morning briefs, digest requests, and recent signal summaries.
---

# signal-loom-brief

Render the grouped markdown digest from the signal-loom index.

## Steps

1. Resolve `ROOT` as described in `$signal-loom-enrich`.

2. Run the brief:
   ```bash
   ROOT="$ROOT" env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY /bin/sh -c 'uv run --project "$ROOT" python -m core.brief \
       --config "$ROOT/config/signal-loom.yaml" \
       --since 7d'
   ```

3. For shareable or archived briefs, offer link verification:
   ```bash
   ROOT="$ROOT" env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY /bin/sh -c 'uv run --project "$ROOT" python -m core.brief \
       --config "$ROOT/config/signal-loom.yaml" \
       --since 7d \
       --verify'
   ```

## Rules

- This is read-only unless the user explicitly asks to save a brief.
- Warn that `--verify` sends HEAD requests to each unique URL.
