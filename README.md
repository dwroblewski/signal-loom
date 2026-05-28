# signal-loom

A personal intelligence pipeline: **declarative sources → scrape → AI-enrich → index → brief.**

Point it at the feeds, channels, and blogs you follow; it scrapes new items, enriches each with structured metadata (summary, topics, entities), builds a queryable index, and produces a verified daily brief grouped by topic. Domain-agnostic — bring your own sources and your own topic vocabulary (AI, climate, finance, healthcare, whatever you track).

Ships two ways from one repo:
- **A Claude Code plugin** — the interactive interface (`/pipeline`, `/enrich`, `/brief`).
- **A pip-installable Python core** — the same pipeline headless, for cron/launchd (`python -m core.pipeline`).

---

## Quickstart (Claude Code plugin)

```
/plugin marketplace add dwroblewski/signal-loom
/plugin install signal-loom
```

Then, in a session:

1. **Set your key** — `export ANTHROPIC_API_KEY=sk-ant-...` (enrichment runs against the Anthropic API; see [Cost](#cost)).
2. **Configure your sources** — copy the examples and edit (run from `${CLAUDE_PLUGIN_ROOT}`, the directory where signal-loom was installed):
   ```
   cp ${CLAUDE_PLUGIN_ROOT}/config/sources.example.yaml      ${CLAUDE_PLUGIN_ROOT}/config/sources.yaml
   cp ${CLAUDE_PLUGIN_ROOT}/config/signal-loom.example.yaml  ${CLAUDE_PLUGIN_ROOT}/config/signal-loom.yaml
   cp ${CLAUDE_PLUGIN_ROOT}/config/topics.example.yaml       ${CLAUDE_PLUGIN_ROOT}/config/topics.yaml          # your topic vocabulary
   cp ${CLAUDE_PLUGIN_ROOT}/config/entity-aliases.example.yaml ${CLAUDE_PLUGIN_ROOT}/config/entity-aliases.yaml
   ```
3. **Run it** — `/pipeline` scrapes new items, asks before enriching (cost-aware), and rebuilds the index. Then `/brief --verify` for a topic-grouped digest with live-link checks.

`uv` must be on your PATH (the bootstrap hook installs deps on first session). Install it: <https://docs.astral.sh/uv/>.

## Headless / scheduled

The same pipeline runs without Claude Code — point cron/launchd at:

```bash
uv run python -m core.pipeline --once         # scrape + enrich + index, one pass
uv run python -m core.pipeline --once --no-enrich   # scrape + index only (free)
uv run python -m core.pipeline --once --dry-run     # preview, no writes
```

Needs `ANTHROPIC_API_KEY` for enrichment. Install for headless use with `uv sync` (or `pip install -e .`).

---

## Configuration

| File | What |
|---|---|
| `config/sources.yaml` | Your sources — one block per feed/channel/page. Adding a source is ~8 lines of YAML, no code. |
| `config/topics.yaml` | **Your controlled topic vocabulary** — `topics.primary` on each item must come from this list. This is what makes cross-source grouping work; without it, "ai agents" and "agentic ai" never cluster. |
| `config/entity-aliases.yaml` | `variant: canonical` map so e.g. `Anthropic PBC` and `anthropic` merge to one entity. Empty `{}` is fine. |
| `config/signal-loom.yaml` | Settings: `enrichment_model`, `content_dir`, `index_path`, and paths to the three files above. |

A source block:

```yaml
simon_willison:
  name: "Simon Willison"
  type: rss                 # rss | youtube | listing
  feed_url: "https://simonw.substack.com/feed"
  output_dir: "content/simon-willison"
  tags: ["newsletter"]
  perspective: "Technical/Builder"
  scrape_limit: 10
  enabled: true
  # keyword_filter: { mode: any, include: ["agents", "evals"] }   # optional, pre-fetch filter
```

**Output is plain markdown + YAML frontmatter** in `content/`, and a queryable `index.json`. That tree is simultaneously an Obsidian vault, a plain folder, and a static-site source — use whatever you like downstream.

## Source types & optional extras

The default install is free and key-light (only an LLM key). Heavier capabilities are opt-in `uv` extras; a source that needs one fails with an actionable message, never a silent skip.

| Capability | Install | Source types | Cost / keys |
|---|---|---|---|
| **core** (default) | `uv sync` | `rss` (incl. Substack), `youtube` (free captions), `listing` (static HTML index) | Free — LLM key only |
| **browser** | `uv sync --extra browser` | `listing` / `fetch_method: browser` for JS-rendered or anti-bot pages | Free (Playwright) |

> Podcast/Whisper transcription, `/signal-scan`, `/search`, full-Substack fetch, and HTML/PDF report rendering are planned for v1.1.

---

## Cost

Headless / non–Claude-Code enrichment hits the Anthropic API. **Measured 2026-05-27** on real full-length Substack articles via `claude-sonnet-4-6` (avg ~6.3K input + ~0.9K output tokens/article):

| Model | $/article | @20 articles/day | @50/day |
|---|---|---|---|
| Haiku 4.5 (est.) | ~$0.011 | ~$7/mo | ~$16/mo |
| **Sonnet 4.6 (measured)** | **~$0.032** | **~$19/mo** | **~$48/mo** |
| Opus 4.7 (est.) | ~$0.16 | ~$96/mo | ~$240/mo |

`enrichment_model` is the main cost lever — default Sonnet, drop to Haiku if cost dominates quality. Cost scales with article length; short items are cheaper.

**On prompt caching:** signal-loom does *not* enable it by default. The enrichment prefix (~1.5K tokens) is below the model cache minimums (2048 Sonnet / 4096 Haiku), so caching writes but never reads — a net loss at this size (measured). The real lever for high volume is the **Message Batches API (50% off, planned v1.1)** — at 20/day that's ~$10/mo on Sonnet. Enable `ApiEnricher(cache_system=True)` only if you grow the spec/vocab prefix past the cache floor.

---

## Security & trust model

Scraped web content is **untrusted input fed to an LLM** — a hostile page could embed prompt-injection ("ignore instructions, run …"). Controls:

- **The enricher sub-agent has no tools** (`agents/enricher.md`, `tools: []`) — it can only return text, so injected instructions to *act* have nothing to act with.
- **Output is data, never instructions.** Every enrichment result is parsed and run through `core/validate.py`'s **allow-list** (only `enriched`, `summary`, `topics`, `entities`, `key_takeaways` survive, with type/size checks) before anything is written. Smuggled keys are dropped.
- **The fetch layer is read-only and source-scoped** — it fetches only the `feed_url`s you declared; it follows no links found in content.
- **No secrets in scraped-content context** — enrichment prompts carry only the article text and the spec.

---

## Differences from the original

signal-loom is extracted from a personal Obsidian-vault pipeline. Intentional simplifications for a shareable, domain-agnostic tool:

- **Tags live in frontmatter** (`tags: [...]`), not line-1 hashtags.
- **Enrichment schema is "rich-minimal"** — dropped the vault's `relevance` scoring, `content_type`, `claims`, and AI-specific canonical-entity *content*. Kept the *mechanisms*: a user-supplied controlled topic vocabulary and an entity-alias map.
- **`ANTHROPIC_API_KEY` is the default** for every path. `claude -p` is not shipped as a backend (subscription use is personal, not for distributed tooling). The only free path is enriching interactively inside your own Claude Code session via sub-agents.

---

## Development

```bash
uv sync --extra dev
uv run pytest -q            # full suite
uv run pytest -m skeleton   # the end-to-end contract test
```

CI runs `pytest` on the no-key path (all LLM calls are faked or replayed from a recorded fixture), so it never spends money. Architecture and rationale: see the design spec and implementation plan referenced in the repo history.

MIT licensed.
