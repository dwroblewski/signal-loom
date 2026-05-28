# signal-loom

A personal intelligence pipeline: **declarative sources → scrape → AI-enrich → index → brief.**

Point it at the feeds, channels, and blogs you follow; it scrapes new items, enriches each with structured metadata (summary, topics, entities), builds a queryable index, and produces a verified daily brief grouped by topic. Domain-agnostic — bring your own sources and your own topic vocabulary (AI, climate, finance, healthcare, whatever you track).

Ships three ways from one repo:
- **A Codex plugin** — `$signal-loom-pipeline`, `$signal-loom-enrich`, and `$signal-loom-brief`; Codex does interactive model work through the active Codex session.
- **A Claude Code plugin** — `/pipeline`, `/enrich`, and `/brief`; Claude sub-agents handle interactive enrichment.
- **A pip-installable Python core** — the same scrape/enrich/index/brief pipeline headless, for cron/launchd (`python -m core.pipeline`).

---

## Quickstart (Codex plugin)

The Codex path is the seat-auth path: Python emits bounded work packets,
validates model output, writes frontmatter, and builds the index. Codex itself
does the model work in your active Codex session. The plugin code does not read
`~/.codex/auth.json` and does not require `OPENAI_API_KEY`, `CODEX_API_KEY`, or
`ANTHROPIC_API_KEY` for Codex-native enrichment.

For local e2e verification from this checkout:

```bash
uv run python scripts/codex_plugin_e2e.py
```

For manual Codex runs, start Codex with API-key variables removed and isolate
zsh startup so local shell profiles cannot re-export them:

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
  '$signal-loom-pipeline refresh my sources'
```

Current Codex CLI builds may not fire plugin `SessionStart` hooks. The runtime
commands still lazy-bootstrap `config/*.yaml` from examples before use; the e2e
harness records whether hook bootstrap was observed.

`allow_login_shell=false` narrows Codex shell startup, but it does not replace
`ZDOTDIR` on this machine; zsh startup can still re-export keys without the
isolated dotdir.

## Quickstart (Claude Code plugin)

```
/plugin marketplace add dwroblewski/signal-loom
/plugin install signal-loom
```

Then, in a session:

1. **Set your key** — `export ANTHROPIC_API_KEY=sk-ant-...` (Claude/headless API enrichment runs against the Anthropic API; see [Cost](#cost)).
2. **Configure your sources** — on first session the plugin creates `config/*.yaml` from the examples automatically. Edit them (your editor / ask Claude to open `${CLAUDE_PLUGIN_ROOT}/config/sources.yaml`). If you want durable config that survives plugin updates, set `SIGNAL_LOOM_CONFIG=/stable/path/signal-loom.yaml`.
3. **Run it** — `/pipeline` scrapes new items, asks before enriching (cost-aware), and rebuilds the index. Then `/brief --verify` for a topic-grouped digest with live-link checks.

`uv` must be on your PATH (the bootstrap hook installs deps on first session). Install it: <https://docs.astral.sh/uv/>.

## Headless / scheduled

The same pipeline runs without Claude Code. **Start with `--no-enrich` (free) to verify your sources work before spending on enrichment:**

```bash
uv run python -m core.pipeline --once --no-enrich   # scrape + index only (free — start here)
uv run python -m core.pipeline --once               # scrape + enrich + index (bills Anthropic API)
uv run python -m core.pipeline --once --dry-run     # preview feeds, no writes
uv run python -m core.pipeline --once --max-enrich 10  # enrich at most 10 files per run
```

Enrichment bills per article — see [Cost](#cost). Run `--no-enrich` first to check your sources work, then add enrichment once you're satisfied. Set `ANTHROPIC_API_KEY` before enriching; the pipeline exits with a clear error if it's unset.

From a cloned repo, config files are auto-created from `*.example.yaml` on first run. From an arbitrary directory, set `SIGNAL_LOOM_CONFIG=/path/to/signal-loom.yaml`.

Install for headless use with `uv sync` (or `pip install -e .`).

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

### Configuration reference

**`signal-loom.yaml` keys:**

| Key | Default | Description |
|---|---|---|
| `enrichment_model` | `claude-sonnet-4-6` | Anthropic model used for enrichment |
| `content_dir` | `../content` from `config/signal-loom.yaml`, otherwise `content` | Where scraped markdown files are written |
| `index_path` | `../index.json` from `config/signal-loom.yaml`, otherwise `index.json` | Output path for the queryable index |
| `sources_path` | `sources.yaml` | Path to sources config, resolved relative to `signal-loom.yaml` |
| `topics_path` | `topics.yaml` | Path to controlled vocabulary, resolved relative to `signal-loom.yaml` |
| `aliases_path` | `entity-aliases.yaml` | Path to entity aliases map, resolved relative to `signal-loom.yaml` |

Relative paths are resolved relative to the config file's directory.

**Source block keys:**

| Key | Required | Description |
|---|---|---|
| `type` | yes | `rss` \| `youtube` \| `listing` |
| `feed_url` | yes | RSS feed URL, YouTube channel URL, or listing page URL |
| `output_dir` | yes | Relative path where scraped files are written (no `..`) |
| `name` | no | Display name (defaults to the YAML key) |
| `tags` | no | List of tags added to every file's frontmatter |
| `perspective` | no | Author perspective string added to frontmatter |
| `scrape_limit` | no (10) | Max items scraped per run |
| `scrape_full_content` | no (false) | For `rss`: fetch full article body instead of feed excerpt |
| `enabled` | no (true) | Set `false` to skip this source without deleting it |
| `keyword_filter` | no | `{mode: any\|all, include: [str]}` — filter items by keyword match |
| `fetch_method` | no (`auto`) | For `listing`: `auto` (direct HTTP first, browser fallback) \| `browser` (always Playwright, requires `uv sync --extra browser`) \| `auto-no-browser` (direct HTTP only) |
| `listing_link_pattern` | no | Regex to extract article links from the listing page (default: broad path heuristic) |

## Source types & optional extras

The default install is free and key-light (only an LLM key). Heavier capabilities are opt-in `uv` extras; a source that needs one fails with an actionable message, never a silent skip.

| Capability | Install | Source types | Cost / keys |
|---|---|---|---|
| **core** (default) | `uv sync` | `rss` (incl. Substack), `youtube` (free captions), `listing` (static HTML index) | Free — LLM key only |
| **browser** | `uv sync --extra browser` | `listing` / `fetch_method: browser` for JS-rendered or anti-bot pages | Free (Playwright) |

**Academic / research papers** work through the `rss` type with no extra setup — point a source at an arXiv category feed (`https://rss.arxiv.org/rss/cs.AI`) or the arXiv Atom API query URL (keyword/author/category control); bioRxiv/medRxiv, PubMed, and journal RSS work the same way. The abstract becomes the body and feeds enrichment cleanly. See the commented `arxiv_cs_ai` block in `config/sources.example.yaml`. (A query-driven `academic` source type — OpenAlex-backed, 250M works, no key — is planned for v1.1.)

> Podcast/Whisper transcription, the `academic` source type, `/signal-scan`, `/search`, full-Substack fetch, and HTML/PDF report rendering are planned for v1.1.

---

## Cost

Headless enrichment and any explicit Anthropic API run hit the Anthropic API.
The Codex-native plugin path does not call OpenAI or Anthropic APIs from Python;
it uses the active Codex session for model work. **Measured 2026-05-27** on real
full-length Substack articles via `claude-sonnet-4-6` (avg ~6.3K input + ~0.9K
output tokens/article):

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
- **The fetch layer is source-scoped and egress-guarded** — it fetches only the `feed_url`s you declared, plus article links the `listing` adapter extracts from those pages. An SSRF egress guard (`_assert_safe_url`) rejects non-http(s) schemes, private/loopback/link-local/reserved/cloud-metadata addresses, and known cloud-metadata hostnames (including on every redirect hop before following). Note: a `listing_link_pattern` that captures absolute URLs widens what is followed — keep patterns path-relative unless you trust the source completely.
- **No secrets in scraped-content context** — enrichment prompts carry only the article text and the spec.

---

## Differences from the original

signal-loom is extracted from a personal Obsidian-vault pipeline. Intentional simplifications for a shareable, domain-agnostic tool:

- **Tags live in frontmatter** (`tags: [...]`), not line-1 hashtags.
- **Enrichment schema is "rich-minimal"** — dropped the vault's `relevance` scoring, `content_type`, `claims`, and AI-specific canonical-entity *content*. Kept the *mechanisms*: a user-supplied controlled topic vocabulary and an entity-alias map.
- **Headless API enrichment remains Anthropic-backed.** `ANTHROPIC_API_KEY` is required when `core.pipeline` runs enrichment without `--no-enrich`.
- **Interactive Claude and Codex paths stay separate.** Claude Code uses top-level `skills/` and `hooks/hooks.json`; Codex uses `.codex-plugin/plugin.json`, `codex/skills/`, and `hooks/codex-hooks.json`.
- **Codex-native enrichment uses the active Codex session.** Python never reads Codex auth files or requires API keys on that path; use the guarded launch form above if your login shell exports API keys.

---

## Development

```bash
uv sync --extra dev
uv run pytest -q            # full suite
uv run pytest -m skeleton   # the end-to-end contract test
uv run python scripts/codex_plugin_e2e.py  # real Codex plugin install/use/writeback smoke
```

**Reproducible installs:** use `uv sync` (reads `uv.lock`) rather than `pip install -e .` — `pyproject.toml` dependency floors are intentionally open-ended for compatibility, while `uv.lock` pins the exact versions used in development and CI.

CI runs `pytest` on the no-key path (all LLM calls are faked or replayed from a recorded fixture), so it never spends money. Architecture and rationale: see the design spec and implementation plan referenced in the repo history.

MIT licensed.
