---
name: pipeline
description: Run the full signal-loom ingestion loop interactively — scrape configured sources, enrich new articles, rebuild the index. Use on "/pipeline", "refresh sources", "check for new content", "run the pipeline".
---

# pipeline

Drives the scrape → enrich → index loop interactively (the headless equivalent is `python -m core.pipeline`, run on a schedule). Use this for on-demand runs and retries.

## Steps

1. **Scrape + index (skip enrich for now)** so we can report what's new before spending on enrichment:
   ```
   uv run --project ${CLAUDE_PLUGIN_ROOT} python -m core.pipeline --once --no-enrich
   ```
   Report: how many new files per source, any fetch warnings (e.g. a source needing `uv sync --extra browser`), total new.

2. **If there are unenriched files, ask the user** whether to enrich now (enrichment costs API tokens — see the cost table in the README). Offer: enrich now / skip / show the files first.

3. **If yes, invoke the `enrich` skill** — it dispatches parallel `enricher` sub-agents and routes results through `core.enrichment_writeback`. (Interactive enrichment uses the sub-agent path; the scheduled `python -m core.pipeline` uses the Anthropic API path. Both share one spec, schema, and writeback module.)

4. **Rebuild the index:**
   ```
   uv run --project ${CLAUDE_PLUGIN_ROOT} python -m core.index
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
