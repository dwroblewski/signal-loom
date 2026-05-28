# Codex Plugin Hardening Report

## Scope

This package addresses the adversarial review of the Codex plugin spike:

- Add a real Codex plugin e2e harness.
- Harden the no-API-key Codex path.
- Keep the Claude/Anthropic close path unchanged.
- Make failed single-file writebacks visible in the re-run queue.
- Prove, or explicitly fail to prove, plugin hook bootstrap.

## Implemented

- `scripts/codex_plugin_e2e.py` installs the local checkout through a temporary
  Codex marketplace, runs real `codex exec` with plugin skills enabled, invokes
  the installed signal-loom `$enrich` skill, applies writeback, rebuilds the index, verifies the
  installed cache output, and cleans up the marketplace/plugin/cache.
- The harness removes `OPENAI_API_KEY`, `CODEX_API_KEY`, and
  `ANTHROPIC_API_KEY`, sets `ZDOTDIR` to an empty temp directory, and passes a
  Codex shell-environment exclude policy.
- A follow-up Codex capability spike added `forced_login_method="chatgpt"`,
  `allow_login_shell=false`, and `shell_environment_policy.experimental_use_profile=false`
  to the real e2e command. Local evidence still requires `ZDOTDIR`; non-login
  shell mode alone did not stop `OPENAI_API_KEY` from reappearing.
- Codex skills now run signal-loom Python commands through guarded child shells
  so plugin code does not depend on API-key environment variables.
- `core.enrichment_writeback apply` now appends validation failures to
  `failed-enrichments.jsonl` for single-file interactive runs.
- README and the Codex spike doc now describe three supported modes: Codex
  plugin, Claude Code plugin, and headless Python.

## Hook Finding

Codex CLI 0.134 did not fire this plugin's `SessionStart` hook during real
local characterization, even with `--enable plugins`, `--enable hooks`, and
`--dangerously-bypass-hook-trust`.

The current supported behavior is therefore:

- Runtime commands lazy-bootstrap config through `ensure_configs(...)`.
- The e2e harness records `hook_bootstrap`.
- `scripts/codex_plugin_e2e.py --require-hook` exists as a future compatibility
  gate, but it is expected to fail on the observed local Codex CLI build.

## Evidence Commands

```bash
env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY uv run pytest -q \
  tests/test_codex_plugin.py \
  tests/test_codex_e2e_harness.py \
  tests/test_clis.py \
  tests/test_bootstrap.py \
  tests/test_enrichment_packets.py

env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY uv run pytest -q

uv run python scripts/codex_plugin_e2e.py
```

Observed local evidence on 2026-05-28:

- Focused Codex/plugin/writeback tests: `20 passed`.
- Full regression: `180 passed, 1 deselected`.
- Real Codex e2e summary:
  - `codex_final_checkpoints`: `guarded_env`, `index_entry`, `skill_used`, `writeback`
  - `frontmatter_enriched`: `true`
  - `index_matches`: `1`
  - `index_enriched`: `true`
  - `hook_bootstrap`: `false`

## Residuals

- Plugin SessionStart hook support remains platform-dependent. Do not claim hook
  bootstrap as proven unless the e2e reports `hook_bootstrap: true`.
- The Codex-native enrichment loop is still interactive agent work, not a
  completely unattended background API pipeline. That is intentional while the
  goal is to leverage the active Codex seat rather than API keys.

## Adversarial Review

- Attack: the user's login shell re-exports `OPENAI_API_KEY` after `codex exec`
  starts. Mitigation: the e2e sets `ZDOTDIR` to an empty temp directory, strips
  API-key variables, passes Codex shell exclusions, and requires a
  `guarded_env: absent` final checkpoint.
- Attack: Codex plugin hooks silently do not fire, so first-run config is
  missing. Mitigation: hook bootstrap is measured separately, runtime commands
  still call lazy config bootstrap, and `--require-hook` can promote this to a
  hard failure when Codex hook behavior changes.
- Attack: model output is malformed during interactive Codex enrichment.
  Mitigation: `core.enrichment_writeback` remains the only write path and now
  queues single-file validation failures for rerun.
- Attack: Codex changes regress the Claude/API close path. Mitigation: focused
  tests still prove missing `ANTHROPIC_API_KEY` fails API enrichment and
  `--no-enrich` skips the key check.
