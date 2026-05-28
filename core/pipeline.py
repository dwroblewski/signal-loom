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
import logging
import sys
from pathlib import Path
from typing import Optional

import frontmatter

from core.config import load_settings, load_sources, load_vocabulary, load_aliases
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
        default="config/signal-loom.yaml",
        metavar="PATH",
        help="Path to signal-loom settings YAML (default: config/signal-loom.yaml).",
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
        help="Report what would be done without writing any files.",
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
        settings = load_settings(args.config)
    except FileNotFoundError as exc:
        logger.error("config file not found: %s", exc)
        return 1
    except Exception as exc:
        logger.error("failed to load settings from %s: %s", args.config, exc)
        return 1

    try:
        sources = load_sources(settings.sources_path)
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

    logger.info(
        "pipeline: %d sources, %d topics, %d aliases",
        len(sources),
        len(vocabulary),
        len(aliases),
    )

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
    # 3. Scrape each source                                                #
    # ------------------------------------------------------------------ #
    all_new_files: list[Path] = []

    for src in sources:
        logger.info("scraping source: %s (%s)", src.name, src.type)
        if args.dry_run:
            logger.info("dry-run: skipping scrape for %s", src.name)
            continue
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
            continue

    logger.info("pipeline: %d total new file(s) scraped", len(all_new_files))

    # ------------------------------------------------------------------ #
    # 4. Enrich new files                                                  #
    # ------------------------------------------------------------------ #
    if not args.no_enrich and not args.dry_run:
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
        logger.info("pipeline: %d file(s) to enrich", len(to_enrich))

        if to_enrich:
            # Build enricher — real or fake seam
            if args.inject_enricher == "fake":
                enricher = _FakeEnricher(vocabulary)
                logger.debug("pipeline: using fake enricher seam")
            else:
                from core.enrich import ApiEnricher
                enricher = ApiEnricher(settings.enrichment_model)

            succeeded = 0
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
                        succeeded += 1
                        logger.debug("enriched: %s", path.name)
                    else:
                        logger.warning(
                            "enrichment writeback failed for %s: %s",
                            path.name,
                            result.errors,
                        )
                except Exception as exc:
                    logger.warning("enrichment error for %s: %s", path, exc)

            logger.info(
                "pipeline: enrichment done — %d/%d succeeded",
                succeeded,
                len(to_enrich),
            )

    # ------------------------------------------------------------------ #
    # 5. Rebuild index                                                     #
    # ------------------------------------------------------------------ #
    if not args.dry_run:
        try:
            result = index_mod.build_index(settings.content_dir, settings.index_path)
            n = len(result.get("entries", []))
            logger.info("pipeline: index rebuilt — %d enriched entries", n)
        except Exception as exc:
            logger.error("index build failed: %s", exc)
            return 1

    logger.info("pipeline: pass complete")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    raise SystemExit(main())
