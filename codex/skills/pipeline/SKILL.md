---
name: pipeline
description: Codex-native signal-loom pipeline. Use on "$pipeline", "refresh sources", "check for new content", or "run the pipeline" from Codex.
---

# pipeline

Run the scrape -> optional Codex-native enrich -> index loop from Codex.

## Steps

1. Resolve `ROOT` as described in `$enrich`.
2. Resolve `CONFIG` as described in `$enrich`. Use a user-supplied config path
   or `SIGNAL_LOOM_CONFIG` when present; otherwise default to
   `$ROOT/config/signal-loom.yaml`.

3. Scrape and index without paid API enrichment:
   ```bash
   ROOT="$ROOT" CONFIG="$CONFIG" env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY /bin/sh -c 'uv run --project "$ROOT" python -m core.pipeline \
       --once \
       --no-enrich \
       --config "$CONFIG"'
   ```

4. If unenriched files remain, ask whether to enrich now. Use `$enrich` for
   that path so Codex, not Python API clients, does model work.

5. After enrichment, rebuild the index and offer `$brief`.

## Rules

- Do not run `core.pipeline` without `--no-enrich` unless the user explicitly
  wants the Anthropic API path.
- Keep the Claude/Anthropic path intact; this skill is the Codex-native path.
