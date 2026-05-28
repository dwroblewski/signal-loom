# Codex Capabilities Research Spike

Date: 2026-05-28

## Goal

Challenge the Codex plugin assumptions against current official Codex docs and
the local Codex CLI. The specific questions were whether signal-loom was using
the wrong auth model, missing a more native plugin feature, or leaning on a
workaround that Codex can now replace.

## Sources

- OpenAI Codex plugin build docs: <https://developers.openai.com/codex/plugins/build>
- OpenAI Codex config reference: <https://developers.openai.com/codex/config-reference>
- OpenAI Codex CLI reference: <https://developers.openai.com/codex/cli/reference>
- OpenAI Codex MCP docs: <https://developers.openai.com/codex/mcp>

## Local Evidence

- `codex --version`: `codex-cli 0.134.0`
- `codex login status` with API-key variables removed: `Logged in using ChatGPT`
- `codex features list`: `plugins`, `hooks`, and `apps` are stable and enabled.
- A minimal temporary plugin installed as `installed, enabled`, and Codex loaded
  its `$smoke` skill from `~/.codex/plugins/cache/...`.
- That same minimal plugin's `SessionStart` marker hook did not run under
  `codex exec --enable plugins --enable hooks --dangerously-bypass-hook-trust`.

## Findings

### Seat Auth Is The Right Codex-Native Path

Codex already supports ChatGPT login, device auth, API keys, and access-token
stdin login. This machine is logged in through ChatGPT, so the plugin should
continue treating the active Codex session as the model worker.

The important hardening knob is `forced_login_method = "chatgpt"`. Use it in
the real e2e and documented manual runs so an environment API key cannot quietly
change the test into an API-key path.

Do not use MCP OAuth as the model-auth answer. Codex MCP OAuth is for
streamable HTTP MCP servers that support OAuth. It authenticates that MCP
server, not the Codex model provider or the ChatGPT seat.

### Project Config Cannot Force Provider Or Auth

The config reference says project-local `.codex/config.toml` cannot override
provider, auth, profile selection, or telemetry routing keys. That means a repo
should not try to enforce seat auth with project-local config.

Correct durable shapes:

- Document a user-level profile in `$CODEX_HOME/signal-loom-codex.config.toml`.
- Or keep the e2e self-contained with `codex exec -c ...` overrides.

### `allow_login_shell=false` Helps But Does Not Replace `ZDOTDIR`

Official config has these useful shell controls:

- `allow_login_shell`
- `shell_environment_policy.exclude`
- `shell_environment_policy.experimental_use_profile`
- `shell_environment_policy.include_only`
- `shell_environment_policy.inherit`
- `shell_environment_policy.set`

Local probe results with outer `OPENAI_API_KEY`, `CODEX_API_KEY`, and
`ANTHROPIC_API_KEY` removed:

| Probe | Result |
|---|---|
| `shell_environment_policy.exclude` only | `OPENAI_API_KEY=present` |
| plus `allow_login_shell=false` | `OPENAI_API_KEY=present` |
| plus `experimental_use_profile=false` | `OPENAI_API_KEY=present` |
| plus isolated `ZDOTDIR` | all three forbidden variables absent |

`allow_login_shell=false` changed Codex shell commands from `zsh -lc` to
`zsh -c`, but this machine still reintroduced `OPENAI_API_KEY`, consistent with
zsh startup such as `.zshenv`. Keep `ZDOTDIR` in the e2e and manual hardening
docs. Add `allow_login_shell=false` anyway because it narrows shell startup
behavior and matches Codex's documented control surface.

### Marketplace Metadata Was Too Loose

The official marketplace metadata docs say each plugin entry should include
`policy.installation`, `policy.authentication`, and `category`. The previous
e2e marketplace worked with top-level defaults, but that shape was not aligned
with current docs.

Fix: make the e2e marketplace plugin entry carry its own policy and category.

### Plugin Hook Docs Are Stronger Than Local Behavior

The plugin docs say:

- Enabled plugins can load lifecycle hooks.
- Plugin hooks are non-managed hooks and require user trust.
- `./hooks/hooks.json` is the default hook file.
- A manifest `hooks` field overrides the default hook file.
- Plugin hook commands receive `PLUGIN_ROOT` and `PLUGIN_DATA`.
- Codex also sets `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` for
  compatibility.

Signal Loom's current split is docs-compatible:

- Claude uses `hooks/hooks.json` and `${CLAUDE_PLUGIN_ROOT}`.
- Codex uses `hooks/codex-hooks.json` and `${PLUGIN_ROOT}`.
- The Codex manifest's custom hook path is allowed by the docs.

However, local Codex CLI 0.134.0 did not run plugin `SessionStart` hooks under
real signal-loom e2e or under a minimal marker-hook plugin, even with hook trust
bypassed. Since the minimal plugin's skill loaded correctly, this is not a
Signal Loom skill-discovery issue.

Keep lazy config bootstrap as the supported path. Keep `--require-hook` as a
future promotion gate, but do not make hook bootstrap required today.

## Wrong Or Incomplete Assumptions

- Wrong: `allow_login_shell=false` can replace `ZDOTDIR`.
  Local evidence shows it cannot on this machine.
- Wrong: plugin-hook failure proves the Signal Loom hook manifest is malformed.
  A minimal plugin also failed to run a marker hook while its skill loaded.
- Incomplete: the e2e marketplace payload omitted docs-required per-plugin
  policy and category metadata.
- Incomplete: the e2e should explicitly force ChatGPT login to prove the seat
  auth path, not merely remove API-key variables.
- Wrong direction: MCP OAuth is not the path for leveraging the Codex seat.
  It is for MCP server auth.

## Recommended Codex-Native Shape

Keep the dual plugin architecture:

- Claude Code path remains top-level `skills/`, `hooks/hooks.json`, and
  Anthropic API-backed headless enrichment.
- Codex path remains `.codex-plugin/plugin.json`, `codex/skills/`, and
  `hooks/codex-hooks.json`.
- Codex-native enrichment remains active-session enrichment: Python emits
  packets and validates writeback; Codex performs model work through ChatGPT
  login.

Use this profile shape for durable local Codex runs:

```toml
forced_login_method = "chatgpt"
allow_login_shell = false

[shell_environment_policy]
experimental_use_profile = false
exclude = ["OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY"]
```

Use `ZDOTDIR="$(mktemp -d)"` around e2e/manual invocations on this machine,
because shell startup still reintroduces `OPENAI_API_KEY` without it.

## Actions Taken In This Spike

- Updated the real Codex e2e command to force ChatGPT auth.
- Added non-login shell and no-profile config to the real Codex e2e command.
- Kept `ZDOTDIR` isolation because local evidence requires it.
- Aligned the temporary local marketplace entry with current Codex docs by
  adding per-plugin `policy` and `category`.

## Open Risk

Codex plugin hooks remain the only unsupported-looking surface locally. The
system is safe because runtime lazy bootstrap covers first-use config creation,
but we should not claim hook bootstrap until the e2e reports
`hook_bootstrap: true` or a future CLI release changes observed behavior.
