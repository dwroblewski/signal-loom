# Codex-Native Plugin Spike

## Goal

Make signal-loom installable in Codex while preserving the Claude Code plugin
path. Codex-native enrichment must not require OpenAI or Anthropic API keys; the
active Codex session performs model work, and Python performs deterministic
packet generation, validation, writeback, and indexing.

## Architecture

- Claude keeps `.claude-plugin/plugin.json`, `hooks/hooks.json`, and top-level
  `skills/`.
- Codex uses `.codex-plugin/plugin.json`, `hooks/codex-hooks.json`, and
  `codex/skills/`.
- `core.enrichment_packets` emits JSONL model-work packets from the same
  canonical prompt builder used by `core.enrich.ApiEnricher`.
- `core.enrichment_writeback` remains the only write path for enrichment output.

## Auth Boundary

Codex plugin code must not read `~/.codex/auth.json` or require API keys for the
Codex-native path. The only model work in the Codex-native path happens inside
the Codex agent session or a trusted `codex exec` invocation.

## Regression Boundary

The Anthropic API path remains unchanged:

- `core.pipeline` still enriches through `core.enrich.ApiEnricher` unless
  `--no-enrich` or a test seam is supplied.
- Missing `ANTHROPIC_API_KEY` still fails before headless API enrichment.
- Existing Claude skills remain in top-level `skills/`.
- Existing Claude hook file remains `hooks/hooks.json`.

## Spike Evidence

- Focused compatibility tests: `uv run pytest tests/test_codex_plugin.py tests/test_bootstrap.py tests/test_enrichment_packets.py`
- Full regression suite: `uv run pytest`
- Result: 172 passed, 1 deselected.

## Residuals

- Codex documents `PLUGIN_ROOT` for plugin hooks, not arbitrary skill shell
  commands. The Codex skills therefore instruct the agent to derive `ROOT` from
  the loaded skill file path when `PLUGIN_ROOT` is absent.
- This spike does not add unattended OpenAI API enrichment. That is intentional:
  the Codex-native path uses the active Codex session for model work so it can
  leverage ChatGPT/Codex account auth without exposing tokens to plugin code.
