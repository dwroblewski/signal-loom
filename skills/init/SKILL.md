---
name: init
description: Scaffold a signal-loom config in the user's project — driven by a short conversation, then written via `python -m core.init`. Use on "/init", "set up signal-loom", "scaffold a signal-loom config", "first time using signal-loom here".
---

# init

Drives the first-run setup. The user has just installed signal-loom in a new
project and the resolver reports "No signal-loom config found." This skill
fills the gap by holding a short conversation, then materializing
`signal-loom.yaml` + `sources.yaml` + `topics.yaml` + `entity-aliases.yaml`
in their project.

## When NOT to use this

- The user already has a working config — even in a nested/non-standard
  location like `config/<name>/signal-loom.yaml`. Don't re-init; run the
  pipeline with `--config <that path>` instead. (Step 1 below catches this.)
- The user just wants to add a source — edit the existing `sources.yaml`.
- The user is in a CI / headless context — direct them to
  `python -m core.init --to <dir>` instead.

## Steps

1. **Check for an existing config FIRST.** Before scaffolding anything, look
   for configs the walk-up resolver may have missed (nested / non-standard
   locations). `core.init` does this for you and **refuses** if it finds any —
   but check explicitly so you can advise the user:
   ```
   find "<target>" -name signal-loom.yaml \
     -not -path '*/.git/*' -not -path '*/.venv/*' -not -path '*/node_modules/*' 2>/dev/null
   ```
   - If this prints a path, **STOP scaffolding.** The project is already set
     up. Tell the user the path and that they should run the pipeline with
     `--config <that path>`. Do NOT run init. Do NOT pass `--force`.
   - If it prints nothing, continue to step 2.

2. **Confirm target directory.** Default to the user's cwd. Show the absolute
   path you'll write into and ask them to confirm. If a `signal-loom.yaml`
   already exists *at the target itself*, STOP — they should edit it or pass
   `--force` explicitly. Do not auto-force.

3. **Set expectations about the bundled config.** There is **one** bundled
   config set (the `minimal` template — also the default). It is NOT blank:
   it ships with a small worked **AI-research** example (Simon Willison's blog,
   Import AI, a YouTube feed) so the pipeline runs out of the box. The user
   will almost certainly replace these with their own sources in step 5.
   - Do not offer `--template <name>` choices: only `minimal` exists. Passing
     any other name errors with "unknown template".

4. **Scaffold:**
   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.init --to "<target>"
   ```
   The command refuses to overwrite, and refuses if it finds other configs
   under the target — both are intentional. Only add `--force` if the user
   explicitly chooses to override one of those refusals.

5. **Walk through the generated files** with the user and replace the example
   content with theirs. The two files that need their input before the
   pipeline is useful:
   - `sources.yaml` — replace the AI example with ≥1 enabled source in their
     domain. (Leftover `enabled: true` AI feeds will otherwise get scraped.)
   - `topics.yaml` — set ≥1 topic in their controlled vocabulary. Don't leave
     it empty — an all-comments topics file fails to load.

   Don't fill these in for them beyond asking, proposing, then editing. The
   right values depend on their domain.

6. **Verify the resolver finds the new config:**
   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.config --print sources_path
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.config --print content_dir
   ```
   `content_dir` should resolve **inside** the project (e.g.
   `<project>/content`), never with a `../` that escapes it. If it errors, the
   user is in the wrong directory or `$HOME` is set unexpectedly — surface the
   error and stop.

7. **Suggest a first pipeline run** with `--no-enrich --dry-run` to confirm
   the source URLs are reachable without spending API tokens. Do not run it
   automatically.

## Rules

- **Existing config beats new scaffold.** If any `signal-loom.yaml` already
  exists under the project, prefer `--config <path>` over init. Never scaffold
  a second config on top of a configured project.
- Never write the config without confirming the target path with the user.
- Never auto-populate `sources.yaml` or `topics.yaml` beyond the bundled
  example. The user's domain decides these.
- Never pass `--force` on the user's behalf. `--force` overrides BOTH the
  overwrite refusal and the existing-config refusal — only the user may choose
  that.
- This skill scaffolds files. It does NOT run the pipeline. After init, the
  user invokes `/pipeline` (or the `pipeline` skill) themselves.
- **Never require `$SIGNAL_LOOM_CONFIG` (or any env var) to run.** It is a
  deprecated legacy fallback in `core.config.resolve_config_path`, not a setup
  step. The supported path: `init` scaffolds `signal-loom.yaml` in the project,
  then the walk-up resolver finds it with no env var set. If you catch yourself
  telling the user to `export SIGNAL_LOOM_CONFIG=…`, stop — fix the config
  location instead (scaffold here, or use `--config <path>`).
- **Must work on a clean clone with zero environment setup.** Verify the
  fresh-install path before claiming done, with the env var UNSET:
  `env -u SIGNAL_LOOM_CONFIG uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.config --print sources_path`
  should print `<project>/sources.yaml` and exit 0. If it only works with the
  env var set, that is the antipattern recurring — fix the root cause, not the env.
- **Don't ship one-off env-var workarounds; align skills with the resolver.**
  All skills (`pipeline`, `brief`, `enrich`) and the README now defer to the
  core resolver and never compute a config path or require an env var. Keep it
  that way: when touching first-run behavior, fix the config location at the
  source rather than reintroducing an `export SIGNAL_LOOM_CONFIG=…` workaround.
