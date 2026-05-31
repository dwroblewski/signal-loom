---
name: pipeline
description: Run the full signal-loom ingestion loop interactively — scrape configured sources, enrich new articles, rebuild the index. Use on "/pipeline", "refresh sources", "check for new content", "run the pipeline".
---

# pipeline

Drives the scrape → enrich → index loop interactively (the headless equivalent is `python -m core.pipeline`, run on a schedule). Use this for on-demand runs and retries.

## Config

Don't compute a config path manually — the core resolver discovers it. Order:

1. `--config <path>` if the user supplied one
2. `$CLAUDE_PLUGIN_OPTION_CONFIG_PATH` (set by Claude Code `userConfig`)
3. `$SIGNAL_LOOM_CONFIG` (legacy; deprecated)
4. Walk up from `$CLAUDE_PROJECT_DIR` (or cwd) looking for `signal-loom.yaml`,
   `.signal-loom.yaml`, `.signal-loom/config.yaml`, or `config/signal-loom.yaml`

If the resolver finds nothing it errors with a "run init" hint — do NOT try to
write a default config from this skill. Tell the user to run the `init` skill
or `python -m core.init --to .` and stop.

Optional CLI override when the user explicitly names a config:

```bash
CONFIG_ARG=""
[ -n "${CONFIG:-}" ] && CONFIG_ARG="--config $CONFIG"
```

## Steps

1. **Scrape + index (skip enrich for now)** so we can report what's new before spending on enrichment:
   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.pipeline --once --no-enrich $CONFIG_ARG
   ```
   Report: how many new files per source, any fetch warnings (e.g. a source needing `uv sync --extra browser`), total new.

   If the command errors with "No signal-loom config found", do NOT auto-create one. The error now scans the project and lists any existing configs it found in nested/non-standard locations (e.g. `config/<name>/signal-loom.yaml`) — **if it lists any, prefer re-running with `--config <that path>`** rather than scaffolding. Only if it finds none, tell the user to run `/signal-loom init` (or `python -m core.init --to .`) and stop.

2. **If there are unenriched files, ask the user** whether to enrich now (enrichment costs API tokens — see the cost table in the README). Offer: enrich now / skip / show the files first.

3. **If yes, invoke the `enrich` skill** — it dispatches parallel `enricher` sub-agents and routes results through `core.enrichment_writeback`. (Interactive enrichment uses the sub-agent path; the scheduled `python -m core.pipeline` uses the Anthropic API path. Both share one spec, schema, and writeback module.)

4. **Rebuild the index:**
   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.index $CONFIG_ARG
   ```

5. **Report** final counts and surface the re-run queue (`failed-enrichments.jsonl`) if any enrichment failed.

6. **Offer to commit** the new `content/` files and `index.json` if the user keeps signal-loom output under version control.

## Flags the user may pass

- `/pipeline --dry-run` → `... --dry-run` (preview scrape, no writes)
- `/pipeline --no-enrich` → scrape + index only
- A source needing the browser extra → tell the user to run `uv sync --extra browser` (the engine emits the actionable message, never a silent skip).

## Rules

- Always invoke core via `uv run --project ${CLAUDE_PLUGIN_ROOT} python -m core.<module>` — skills run from the user's cwd, so `--project` is required for the right environment.
- Never enrich without surfacing the cost first (the headless path bills the Anthropic API).
- Validation/writing lives entirely in `core.enrichment_writeback`; this skill only orchestrates and reports.
