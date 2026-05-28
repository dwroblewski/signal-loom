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

Real e2e testing found one important shell boundary: Codex shell commands may
run through the user's login shell, and that shell startup can re-export
`OPENAI_API_KEY` after the outer `codex exec` process removed it. The hardened
launch pattern is:

```bash
ZDOTDIR="$(mktemp -d)" \
env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY \
codex exec \
  --enable plugins \
  --enable hooks \
  -c 'forced_login_method="chatgpt"' \
  -c 'allow_login_shell=false' \
  -c 'shell_environment_policy.experimental_use_profile=false' \
  -c 'shell_environment_policy.exclude=["OPENAI_API_KEY","CODEX_API_KEY","ANTHROPIC_API_KEY"]' \
  '$pipeline refresh my sources'
```

The 2026-05-28 capability spike tested `allow_login_shell=false` directly. It
changed Codex shell commands from `zsh -lc` to `zsh -c`, but `OPENAI_API_KEY`
still reappeared without `ZDOTDIR`, so the empty `ZDOTDIR` remains part of the
supported guard on this machine.

Inside skills, every signal-loom Python command also runs through a guarded
child shell:

```bash
ROOT="$ROOT" env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY /bin/sh -c 'uv run --project "$ROOT" ...'
```

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
- Original spike result: 172 passed, 1 deselected.
- Hardening result after real e2e harness and queue changes: 180 passed, 1
  deselected.
- Real plugin e2e harness: `uv run python scripts/codex_plugin_e2e.py`
- The e2e installs the local checkout through a temporary Codex marketplace,
  invokes the installed signal-loom `$enrich` skill, verifies guarded child
  env absence for API-key variables, applies writeback, rebuilds the index, and
  verifies `enriched: true`.

## Residuals

- Codex documents `PLUGIN_ROOT` for plugin hooks, not arbitrary skill shell
  commands. The Codex skills therefore resolve `ROOT` from `PLUGIN_ROOT`, an
  explicit installed root, the current checkout, or the newest installed
  `~/.codex/plugins/cache/*/signal-loom/*` cache.
- This spike does not add unattended OpenAI API enrichment. That is intentional:
  the Codex-native path uses the active Codex session for model work so it can
  leverage ChatGPT/Codex account auth without exposing tokens to plugin code.
- Do not treat MCP OAuth as the seat-auth mechanism. Codex MCP OAuth is for
  authenticating streamable HTTP MCP servers, not the Codex model provider.
- Codex CLI 0.134 did not fire this plugin's `SessionStart` hook in local
  characterization runs, even with plugins, hooks, and trust bypass enabled.
  Runtime commands therefore keep `ensure_configs(...)` lazy-bootstrap as the
  supported path. The e2e harness records `hook_bootstrap` and has a
  `--require-hook` mode for future CLI versions.
