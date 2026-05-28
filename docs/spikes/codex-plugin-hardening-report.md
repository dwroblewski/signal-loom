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

## Codex CLI version sensitivity (verified 2026-05-28)

The original spike evidence (180 passed; e2e checkpoints) was gathered on Codex
CLI **0.134**. Two CLI-version dependencies surfaced when re-running the live
harness on other builds — neither is a signal-loom defect:

- **Plugin install verb requires Codex >= ~0.134.** On **0.129.0** `codex plugin`
  exposes only `marketplace {add,upgrade,remove}` — there is no `codex plugin add`,
  so the harness dies at install (`unrecognized subcommand 'add'`). `codex plugin
  add <plugin>@<marketplace>` exists from 0.134/0.135.
- **`codex exec` defaults to a read-only sandbox on 0.135.** 0.134 ran the
  enrichment writes without an explicit sandbox; **0.135** denies them
  (`writeback: blocked`, `index_entry: blocked` — "writes to the temp dir and
  installed plugin root were denied"). `--sandbox`'s default is `auto`, which
  resolved to read-only here. Fix: `codex_exec_args` now passes
  `--sandbox workspace-write` plus `--add-dir` for the two roots outside the
  primary workspace (the temp dir holding the raw YAML, and `~/.codex/plugins`
  holding the installed plugin cache). This stays short of `danger-full-access`
  and leaves the keyless guard (`guarded_env: absent`) untouched.

**Re-verified live on codex-cli 0.135.0 (2026-05-28), after the sandbox fix:**

- Full regression: `181 passed, 1 deselected`.
- Real Codex e2e summary:
  - `codex_final_checkpoints`: `guarded_env`, `index_entry`, `skill_used`, `writeback`
  - `frontmatter_enriched`: `true`
  - `index_enriched`: `true` · `index_matches`: `1`
  - `index_primary_topics`: `ai agents`, `enterprise ai`, `model releases`
  - `hook_bootstrap`: `false` (expected — see Hook Finding)

## Residuals

- Plugin SessionStart hook support remains platform-dependent. Do not claim hook
  bootstrap as proven unless the e2e reports `hook_bootstrap: true`.
- The Codex-native enrichment loop is still interactive agent work, not a
  completely unattended background API pipeline. That is intentional while the
  goal is to leverage the active Codex seat rather than API keys.
- The live harness tracks the moving Codex CLI surface (plugin verbs, sandbox
  defaults). Re-confirm `codex --version` and the sandbox default when an e2e
  regresses before assuming a signal-loom regression.

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
- Attack: a new Codex CLI silently changes the `codex exec` sandbox default to
  read-only, so deterministic writes fail and the e2e looks like a code
  regression. Mitigation: the harness pins `--sandbox workspace-write` with
  explicit `--add-dir` writable roots (not the CLI default), and the version-
  sensitivity section documents the 0.135 read-only default + the >=0.134
  `codex plugin add` requirement.
