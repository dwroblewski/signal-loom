---
name: brief
description: Build a grouped markdown digest of recent signals from the index, with optional HEAD-check link verification. Use on "/brief", "show me the digest", "what's new", "morning brief", "summarize recent signals".
---

# brief

Reads the signal-loom index, groups entries by their controlled-vocabulary `topics.primary` field, and renders a scannable markdown digest. With `--verify`, each link is HEAD-checked and annotated live / stale / dead — the key differentiator over a plain listing.

## Config

Don't compute a config path manually — the core resolver discovers it. Order:

1. `--config <path>` if the user supplied one
2. `$CLAUDE_PLUGIN_OPTION_CONFIG_PATH` (set by Claude Code `userConfig`)
3. `$SIGNAL_LOOM_CONFIG` (legacy; deprecated)
4. Walk up from `$CLAUDE_PROJECT_DIR` (or cwd) looking for `signal-loom.yaml`,
   `.signal-loom.yaml`, `.signal-loom/config.yaml`, or `config/signal-loom.yaml`

`brief` is a read-only query: if the resolver finds nothing, `core.brief` falls
back to `index.json` in the cwd so ad-hoc inspection still works. Don't write a
default config from this skill — if there's no project config, suggest the user
run the `init` skill (or `python -m core.init --to .`).

Optional CLI override when the user explicitly names a config:

```bash
CONFIG_ARG=""
[ -n "${CONFIG:-}" ] && CONFIG_ARG="--config $CONFIG"
```

## Steps

1. **Run the brief:**
   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.brief \
       $CONFIG_ARG \
       --since 7d
   ```
   For link verification (recommended for sharing or archiving):
   ```
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.brief \
       $CONFIG_ARG \
       --since 7d \
       --verify
   ```

2. **Present the digest** to the user. It contains:
   - `## <primary topic>` group headers (controlled vocabulary — entries sharing a topic genuinely collide)
   - One bullet per entry: `[title](url)` · source · published date + one-line summary snippet
   - With `--verify`: each link annotated ✓ live / ⚠ stale / ✗ dead

3. **Offer to save** the digest to `content/briefs/<date>.md` if the user wants a persistent copy (create the directory first — the shell redirect fails if it doesn't exist):
   ```
   mkdir -p content/briefs
   uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.brief \
       $CONFIG_ARG \
       --since 7d --verify > content/briefs/$(date +%F).md
   ```

## Flags

| Flag | Default | Notes |
|---|---|---|
| `--since` | (none) | ISO date (`2026-05-01`) or relative (`7d`, `30d`) |
| `--until` | (none) | ISO date upper bound |
| `--verify` | off | HEAD-check every unique URL; adds ~1–3s per URL |
| `--limit` | 50 | Cap on entries included |
| `--index` | config-resolved `index_path` | Path to the index file. Defaults to the `index_path` from the resolved config; only falls back to `index.json` in cwd when no config is found. |

## Verification tiers

| Tier | Trigger | Annotation |
|---|---|---|
| live | 2xx or 3xx HTTP status | ✓ live |
| stale | network error, timeout, 5xx | ⚠ stale |
| dead | 404 or 410 | ✗ dead |

## Rules

- Always invoke core via `uv run --project "${CLAUDE_PLUGIN_ROOT}" python -m core.brief` — skills run from the user's cwd, so `--project` is required for the right environment.
- `brief` is a **read-only consumer**: it never writes the corpus or modifies the index.
- With `--verify`, warn the user that each unique URL incurs a HEAD request; on large windows (`--limit 200`+) this adds noticeable latency.
- If the index is missing or empty, surface the error clearly and suggest running `/pipeline` first.
