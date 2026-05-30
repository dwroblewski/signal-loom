---
name: init
description: Scaffold a signal-loom config in the user's project — driven by a short conversation, then written via `python -m core.init`. Use on "/init", "set up signal-loom", "scaffold a signal-loom config", "first time using signal-loom here".
---

# init

Drives the first-run setup. The user has just installed signal-loom in a new
project and the resolver reports "No signal-loom config found." This skill
fills the gap by holding a short conversation to understand what they want to
monitor, then materializing `signal-loom.yaml` + `sources.yaml` +
`topics.yaml` + `entity-aliases.yaml` in their project.

## When NOT to use this

- The user already has a working `signal-loom.yaml` and just wants to add a
  source — edit the existing file directly; don't re-init.
- The user is in a CI / headless context — direct them to
  `python -m core.init --to <dir> --template <name>` instead.

## Steps

1. **Confirm target directory.** Default to the user's cwd. Show the absolute
   path you'll write into and ask them to confirm. If a `signal-loom.yaml`
   already exists there, STOP — they should either edit it or pass `--force`
   explicitly. Do not auto-force.

2. **Pick a template.** Ask one question: "What kind of signals do you want to
   monitor first?" Map their answer to one of the templates available under
   `${CLAUDE_PLUGIN_ROOT}/examples/` (list them with `ls`). If no template
   matches, use `minimal` and tell the user we'll start blank.

3. **Scaffold:**
   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.init \
     --to "<target>" --template "<template>"
   ```
   The command refuses to overwrite by default — that's intentional.

4. **Walk through the generated files** with the user. The two files that need
   their input before the pipeline does anything useful:
   - `sources.yaml` — must contain ≥1 enabled source.
   - `topics.yaml` — must contain ≥1 topic.

   Don't fill these in for them unless they ask. The right defaults depend on
   their domain — ask, propose, then edit.

5. **Verify the resolver finds the new config:**
   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.config --print sources_path
   ```
   This proves walk-up discovery works from cwd. If it errors, the user is in
   the wrong directory or `$HOME` is set unexpectedly — surface the error and
   stop.

6. **Suggest a first pipeline run** with `--no-enrich --dry-run` to confirm
   the source URLs are reachable without spending API tokens. Do not run it
   automatically.

## Rules

- Never write the config without confirming the target path with the user.
- Never auto-populate `sources.yaml` or `topics.yaml` beyond the template's
  example placeholders. The user's domain decides these.
- Never pass `--force` on the user's behalf; if `signal-loom.yaml` exists,
  the user must explicitly choose to overwrite.
- This skill scaffolds files. It does NOT run the pipeline. After init, the
  user invokes `/pipeline` (or the `pipeline` skill) themselves.
