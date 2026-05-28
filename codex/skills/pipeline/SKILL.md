---
name: signal-loom-pipeline
description: Codex-native signal-loom pipeline. Use to refresh sources, optionally enrich via Codex seat auth, and rebuild the signal index.
---

# signal-loom-pipeline

Run the scrape -> optional Codex-native enrich -> index loop from Codex.

## Steps

1. Resolve `ROOT` as described in `$signal-loom-enrich`.

2. Scrape and index without paid API enrichment:
   ```bash
   ROOT="$ROOT" env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY /bin/sh -c 'uv run --project "$ROOT" python -m core.pipeline \
       --once \
       --no-enrich \
       --config "$ROOT/config/signal-loom.yaml"'
   ```

3. If unenriched files remain, ask whether to enrich now. Use
   `$signal-loom-enrich` for that path so Codex, not Python API clients, does
   model work.

4. After enrichment, rebuild the index and offer `$signal-loom-brief`.

## Rules

- Do not run `core.pipeline` without `--no-enrich` unless the user explicitly
  wants the Anthropic API path.
- Keep the Claude/Anthropic path intact; this skill is the Codex-native path.
