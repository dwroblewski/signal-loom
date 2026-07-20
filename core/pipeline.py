"""core/pipeline.py — Headless autopilot for signal-loom.

Entry points
------------
- CLI:        ``python -m core.pipeline``  /  ``signal-loom``
- Public API: ``main(argv=None) -> int``

Flow
----
1. Load settings (``--config``), sources, vocabulary, aliases.
2. For each *enabled* source → ``scrape.run_source(...)`` (injectable via
   ``--_inject-fetch fixture``).
3. Collect newly written files lacking ``enriched: true``.
4. Unless ``--no-enrich``: enrich each new file via the Anthropic API
   (injectable via ``--_inject-enricher fake``) and call
   ``enrichment_writeback.apply``.
5. Rebuild ``index.json`` via ``index.build_index``.
6. Return 0 on success.

Test seams
----------
``--_inject-fetch fixture``
    Replaces the real network fetch_feed / fetch_article calls with a
    synthetic one-item RSS feed and a stub article body so no network I/O
    occurs.

``--_inject-enricher fake``
    Replaces ``ApiEnricher`` with a ``_FakeEnricher`` that returns a
    hard-coded valid YAML string (``enriched: true`` + a vocabulary topic).
    No Anthropic API key is required.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import frontmatter

from core.config import (
    ConfigError,
    ConfigNotFoundError,
    load_aliases,
    load_settings,
    load_sources,
    load_vocabulary,
    resolve_config_path,
    resolve_source_output_dirs,
)
from core import enrichment_writeback, index as index_mod
from core import scrape as scrape_mod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fake enricher (test seam — no API calls)
# ---------------------------------------------------------------------------


class _FakeEnricher:
    """Drop-in for ApiEnricher that returns a fixed valid enrichment YAML.

    The primary topic is chosen as the lexicographically first entry in the
    configured vocabulary so the response always passes vocab validation.
    """

    def __init__(self, vocabulary: set[str]) -> None:
        self._topic = sorted(vocabulary)[0] if vocabulary else "ai agents"

    def enrich(self, content: str, vocabulary: set[str]) -> tuple[str, dict]:
        """Return a synthetic enrichment response without calling the API."""
        raw = (
            "```yaml\n"
            "enriched: true\n"
            "summary: " + "x" * 200 + "\n"
            "topics:\n"
            "  primary:\n"
            f"    - {self._topic}\n"
            "  secondary: []\n"
            "entities:\n"
            "  organizations:\n"
            "    - Acme\n"
            "  people: []\n"
            "```"
        )
        usage = {
            "input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": 0,
            "total_input_tokens": 0,
        }
        return raw, usage


# ---------------------------------------------------------------------------
# Fixture fetch seam (one-item synthetic RSS feed, no network)
# ---------------------------------------------------------------------------

_FIXTURE_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Fixture Feed</title>
    <link>https://fixture.example.com</link>
    <description>Synthetic feed for pipeline tests</description>
    <item>
      <title>Fixture Article About AI Agents</title>
      <link>https://fixture.example.com/article-001</link>
      <pubDate>Mon, 26 May 2026 09:00:00 +0000</pubDate>
      <description>A synthetic article about AI agents for use in automated tests.</description>
      <guid>https://fixture.example.com/article-001</guid>
    </item>
  </channel>
</rss>
"""


def _fixture_fetch_feed(_url: str):
    """Return a parsed feedparser dict from the inline fixture XML."""
    from core.fetch import parse_feed
    return parse_feed(_FIXTURE_RSS_XML)


def _fixture_fetch_article(_url: str) -> str:
    """Return a stub article body (enough words to pass the 100-word gate)."""
    return ("body " * 80).strip()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _needs_enrichment(path: Path) -> bool:
    """Return True if *path* lacks ``enriched: true`` in its frontmatter."""
    try:
        post = frontmatter.load(str(path))
        return post.metadata.get("enriched") is not True
    except Exception:
        return True


def _throttle_wait(throttle_last_fetch: dict[str, float], src) -> str | None:
    """Space same-throttle_group sources by ``src.throttle_seconds``.

    Sleeps if the group was fetched less than ``throttle_seconds`` ago, then
    returns the active group name (so the caller can stamp the completion time),
    or ``None`` when the source has no throttle. Used by BOTH the real scrape
    loop and ``--dry-run`` — dry-run fetches feeds too, so it must throttle or it
    re-hammers rate-limited hosts (e.g. reddit.com) exactly as commit 3ae01f1
    set out to prevent.
    """
    group = src.throttle_group if src.throttle_seconds > 0 else None
    if group:
        last_fetch = throttle_last_fetch.get(group)
        if last_fetch is not None:
            wait_seconds = src.throttle_seconds - (time.monotonic() - last_fetch)
            if wait_seconds > 0:
                logger.info(
                    "throttling group %s for %.1fs before %s",
                    src.throttle_group,
                    wait_seconds,
                    src.name,
                )
                time.sleep(wait_seconds)
    return group


def _append_failed_queue(path: Path, errors: list[str], queue_path: Path) -> None:
    """Append a failed enrichment entry to the re-run queue at *queue_path*."""
    try:
        queue_path.parent.mkdir(parents=True, exist_ok=True)
        with queue_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"path": str(path), "errors": errors}) + "\n")
    except OSError as exc:
        logger.warning("could not write failed-enrichments queue: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """Run a single pipeline pass.

    Args:
        argv: Argument list (defaults to ``sys.argv[1:]``).

    Returns:
        0 on success, 1 on error.
    """
    parser = argparse.ArgumentParser(
        prog="signal-loom",
        description="Headless signal-loom autopilot: scrape → enrich → index.",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to signal-loom settings YAML (default: auto-discovered).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single pass and exit (vs. continuous loop, future).",
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Scrape and rebuild index only; skip enrichment.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Preview what would be scraped without writing any files. "
            "Fetches each enabled source's feed and reports ~N items per source."
        ),
    )
    parser.add_argument(
        "--max-enrich",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Cap the number of files enriched in one run (0 = unlimited). "
            "Deferred files remain in content/ for the next run."
        ),
    )
    # Hidden test seams — prefixed with underscore to signal internal use
    parser.add_argument(
        "--_inject-fetch",
        dest="inject_fetch",
        default=None,
        metavar="MODE",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--_inject-enricher",
        dest="inject_enricher",
        default=None,
        metavar="MODE",
        help=argparse.SUPPRESS,
    )

    args = parser.parse_args(argv)

    # ------------------------------------------------------------------ #
    # 1. Load configuration                                                #
    # ------------------------------------------------------------------ #
    try:
        config_path = resolve_config_path(args.config)
    except ConfigNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        settings = load_settings(config_path)
    except FileNotFoundError as exc:
        logger.error("config file not found: %s", exc)
        return 1
    except Exception as exc:
        logger.error("failed to load settings from %s: %s", config_path, exc)
        return 1

    # Failed-enrichment queue lands beside the resolved config root (same place
    # the writeback CLI uses), NOT the process CWD — a cron run with CWD=$HOME
    # would otherwise strand failure records where /enrich never looks.
    failed_queue_path = enrichment_writeback.default_failed_queue_path(config_path)

    try:
        sources = resolve_source_output_dirs(
            load_sources(settings.sources_path),
            settings,
        )
    except Exception as exc:
        logger.error("failed to load sources from %s: %s", settings.sources_path, exc)
        return 1

    try:
        vocabulary = load_vocabulary(settings.topics_path)
    except Exception as exc:
        logger.error("failed to load vocabulary from %s: %s", settings.topics_path, exc)
        return 1

    try:
        aliases = load_aliases(settings.aliases_path)
    except Exception as exc:
        logger.error("failed to load aliases from %s: %s", settings.aliases_path, exc)
        return 1

    # #7 empty vocab fail-fast — check BEFORE any scraping/enriching
    if not vocabulary:
        print(
            f"{settings.topics_path} has no topics — add at least one",
            file=sys.stderr,
        )
        return 1

    logger.info(
        "pipeline: %d sources, %d topics, %d aliases",
        len(sources),
        len(vocabulary),
        len(aliases),
    )

    # #9 missing API key preflight — check before constructing ApiEnricher
    if not args.no_enrich and not args.dry_run and args.inject_enricher != "fake":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print(
                "ANTHROPIC_API_KEY not set — export it, or re-run with --no-enrich",
                file=sys.stderr,
            )
            return 1

    # ------------------------------------------------------------------ #
    # 2. Build fetch injections                                            #
    # ------------------------------------------------------------------ #
    fetch_feed_fn = None
    fetch_article_fn = None
    if args.inject_fetch == "fixture":
        fetch_feed_fn = _fixture_fetch_feed
        fetch_article_fn = _fixture_fetch_article
        logger.debug("pipeline: using fixture fetch seam")

    # ------------------------------------------------------------------ #
    # 3. Scrape each source  (or dry-run preview)                          #
    # ------------------------------------------------------------------ #
    all_new_files: list[Path] = []
    scrape_errors = 0
    throttle_last_fetch: dict[str, float] = {}

    if args.dry_run:
        # Real dry-run: fetch each feed and count items without writing anything
        print("dry-run preview — no files will be written\n")
        total_items = 0
        for src in sources:
            logger.info("dry-run: checking source: %s (%s)", src.name, src.type)
            throttle_group = None
            try:
                if src.type == "rss":
                    throttle_group = _throttle_wait(throttle_last_fetch, src)
                    _feed_fn = fetch_feed_fn or scrape_mod._default_fetch_feed
                    parsed = _feed_fn(src.feed_url)
                    entries = getattr(parsed, "entries", []) or []
                    n = min(len(entries), src.scrape_limit)
                    print(f"  {src.name} ({src.type}): would scrape ~{n} item(s)")
                    total_items += n
                elif src.type == "youtube":
                    print(f"  {src.name} ({src.type}): would scrape up to {src.scrape_limit} item(s) (feed fetch skipped in dry-run)")
                    total_items += src.scrape_limit
                elif src.type == "listing":
                    print(f"  {src.name} ({src.type}): would scrape up to {src.scrape_limit} item(s) (feed fetch skipped in dry-run)")
                    total_items += src.scrape_limit
                else:
                    print(f"  {src.name}: unsupported type '{src.type}' — would skip")
            except Exception as exc:
                print(f"  {src.name}: feed fetch error — {exc}")
            finally:
                if throttle_group:
                    throttle_last_fetch[throttle_group] = time.monotonic()
        print(f"\ndry-run summary: {len(sources)} source(s), ~{total_items} total item(s) would be scraped")
        return 0

    for src in sources:
        logger.info("scraping source: %s (%s)", src.name, src.type)
        throttle_group = _throttle_wait(throttle_last_fetch, src)
        try:
            new_files = scrape_mod.run_source(
                src,
                fetch_feed=fetch_feed_fn,
                fetch_article=fetch_article_fn,
            )
            logger.info("  wrote %d new file(s) for %s", len(new_files), src.name)
            all_new_files.extend(new_files)
        except Exception as exc:
            logger.warning("scrape failed for source %s: %s", src.name, exc)
            scrape_errors += 1
            continue
        finally:
            if throttle_group:
                throttle_last_fetch[throttle_group] = time.monotonic()

    logger.info("pipeline: %d total new file(s) scraped", len(all_new_files))

    # ------------------------------------------------------------------ #
    # 4. Enrich new files                                                  #
    # ------------------------------------------------------------------ #
    enrich_total = 0
    enrich_succeeded = 0
    failed_queue_written = False

    if not args.no_enrich:
        # Determine which files need enrichment: ALL *.md under content_dir
        # that lack enriched: true, not just files scraped this run.
        # This drains the backlog of files scraped with --no-enrich or that
        # failed enrichment in a previous run (scrape dedup would skip them
        # on re-scrape, so we must scan the directory directly).
        content_dir_path = Path(settings.content_dir)
        to_enrich = [
            f for f in sorted(content_dir_path.rglob("*.md"))
            if _needs_enrichment(f)
        ]
        enrich_total = len(to_enrich)
        logger.info("pipeline: %d file(s) to enrich", enrich_total)

        # Apply --max-enrich cap
        deferred_count = 0
        if args.max_enrich > 0 and len(to_enrich) > args.max_enrich:
            deferred_count = len(to_enrich) - args.max_enrich
            to_enrich = to_enrich[: args.max_enrich]
            logger.info(
                "pipeline: --max-enrich %d cap applied; deferring %d file(s)",
                args.max_enrich,
                deferred_count,
            )

        if to_enrich:
            # Log cost gate info
            model = settings.enrichment_model
            # Rough cost estimates per article (measured 2026-05-27)
            _cost_map = {
                "claude-haiku-4-5": 0.011,
                "claude-sonnet-4-6": 0.032,
                "claude-opus-4-7": 0.16,
            }
            est_cost = _cost_map.get(model, 0.032)
            logger.info(
                "enriching %d file(s) via %s (~$%.3f/article, see README cost table)",
                len(to_enrich),
                model,
                est_cost,
            )

            # Build enricher — real or fake seam
            if args.inject_enricher == "fake":
                enricher = _FakeEnricher(vocabulary)
                logger.debug("pipeline: using fake enricher seam")
            else:
                from core.enrich import ApiEnricher
                enricher = ApiEnricher(settings.enrichment_model)

            for path in to_enrich:
                try:
                    post = frontmatter.load(str(path))
                    content = post.content
                    raw, usage = enricher.enrich(content, vocabulary)
                    result = enrichment_writeback.apply(
                        path,
                        raw,
                        vocabulary=vocabulary,
                        aliases=aliases,
                        retries=2,
                    )
                    if result.ok:
                        enrich_succeeded += 1
                        logger.debug("enriched: %s", path.name)
                    else:
                        logger.warning(
                            "enrichment writeback failed for %s: %s",
                            path.name,
                            result.errors,
                        )
                        # #13 append to failed-enrichments.jsonl queue
                        _append_failed_queue(path, result.errors, failed_queue_path)
                        failed_queue_written = True
                except Exception as exc:
                    logger.warning("enrichment error for %s: %s", path, exc)
                    _append_failed_queue(path, [str(exc)], failed_queue_path)
                    failed_queue_written = True

            logger.info(
                "pipeline: enrichment done — %d/%d succeeded",
                enrich_succeeded,
                len(to_enrich),
            )

        if deferred_count:
            logger.info(
                "pipeline: %d file(s) deferred (--max-enrich cap); re-run to continue",
                deferred_count,
            )

    if failed_queue_written:
        logger.info(
            "pipeline: some enrichments failed — re-run queue written to %s",
            failed_queue_path,
        )

    # ------------------------------------------------------------------ #
    # 5. Rebuild index                                                     #
    # ------------------------------------------------------------------ #
    try:
        result = index_mod.build_index(settings.content_dir, settings.index_path)
        n = len(result.get("entries", []))
        logger.info("pipeline: index rebuilt — %d enriched entries", n)
    except Exception as exc:
        logger.error("index build failed: %s", exc)
        return 1

    logger.info("pipeline: pass complete")

    # ------------------------------------------------------------------ #
    # 6. Exit code                                                         #
    # ------------------------------------------------------------------ #
    # Return nonzero if every source failed to scrape
    if sources and scrape_errors == len(sources):
        logger.error("pipeline: all %d source(s) failed to scrape", len(sources))
        return 1

    # Return nonzero if enrichment was attempted and every file failed
    if not args.no_enrich and enrich_total > 0 and enrich_succeeded == 0:
        # Only fail if we actually tried to enrich (to_enrich was non-empty)
        # and not a case where deferred cap reduced to_enrich to zero
        if enrich_total > 0:
            logger.error("pipeline: enrichment attempted but 0/%d succeeded", enrich_total)
            return 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
