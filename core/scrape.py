"""core/scrape.py — Source adapters: RSS, YouTube, and listing page scrapers.

Ported from internal-pipeline/scripts/scrape_engine.py (RSSAdapter, YouTubeAdapter,
ListingAdapter, dedup logic, get_adapter factory) and pipeline_utils.py
(sanitize_filename, parse_rss_date, file_exists_check helpers).

Key differences from the vault source:
  - No podcast adapter (v1.1).
  - ai_filter/AI_KEYWORDS replaced by generic keyword_filter on SourceConfig
    ({mode: "any"|"all", include: [str]}) — no hardcoded keyword list.
  - Output format: YAML frontmatter (---/yaml/---/body) not line-1 hashtags.
  - No telemetry (emit_pipeline_telem, cycle ids).
  - No vault paths / VAULT_ROOT.
  - Network/IO is injectable for testing via run_source() keyword arguments.
"""

from __future__ import annotations

import html as _html_module
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin

import frontmatter

from core.config import SourceConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers (ported from pipeline_utils.py)
# ---------------------------------------------------------------------------


def _sanitize_filename(title: str, max_len: int = 100) -> str:
    """Convert title to safe filename with clean formatting."""
    title = _html_module.unescape(title)
    # Strip YouTube hashtags (#ai #llm etc.)
    title = re.sub(r"\s*#\w+", "", title)
    # Normalize em/en dashes → regular hyphen with spaces
    title = re.sub(r"[—–]", " - ", title)
    # Normalize curly quotes
    title = title.replace("‘", "'").replace("’", "'")
    title = title.replace("“", "").replace("”", "")
    # Remove problematic filesystem characters
    title = re.sub(r'[<>:"/\\|?*]', "", title)
    # Collapse whitespace
    title = re.sub(r"\s+", " ", title).strip()
    # Truncate at word boundary
    if len(title) > max_len:
        title = title[:max_len].rsplit(" ", 1)[0]
    return title


def _parse_rss_date(pub_date: str) -> str:
    """Parse an RSS pubDate string into ISO date (YYYY-MM-DD)."""
    if not pub_date:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        cleaned = pub_date.strip()
        if re.match(r"^[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4}$", cleaned):
            cleaned += " 00:00:00 GMT"
        dt = parsedate_to_datetime(cleaned)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def _file_exists_check(output_dir: Path, date_str: str, clean_title: str) -> bool:
    """Return True if a file matching date+title already exists in output_dir."""
    prefix = f"{date_str} - {clean_title}"
    for f in output_dir.iterdir() if output_dir.exists() else []:
        if f.name.startswith(prefix):
            return True
    return False


def _strip_html(html_content: Optional[str]) -> str:
    """Remove HTML tags and clean up text."""
    if not html_content:
        return ""
    text = re.sub(r"<[^>]+>", " ", html_content)
    text = _html_module.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Keyword filter
# ---------------------------------------------------------------------------


def _matches_keyword_filter(
    text: str,
    kf: dict[str, Any],
) -> bool:
    """Return True if *text* passes the keyword_filter dict.

    kf must have keys:
      mode: "any" | "all"   — OR vs AND matching
      include: list[str]    — case-insensitive substrings to match
    """
    mode: str = kf.get("mode", "any")
    include: list[str] = kf.get("include", [])
    if not include:
        return True
    lower = text.lower()
    if mode == "all":
        return all(kw.lower() in lower for kw in include)
    # default "any"
    return any(kw.lower() in lower for kw in include)


# ---------------------------------------------------------------------------
# Scraped item dataclass
# ---------------------------------------------------------------------------


class ScrapedItem:
    """Adapter-agnostic scraped content item."""

    __slots__ = ("title", "content", "date", "url", "source_name", "description", "channel")

    def __init__(
        self,
        title: str,
        content: str,
        date: str,
        url: str,
        source_name: str,
        description: str = "",
        channel: str = "",
    ) -> None:
        self.title = title
        self.content = content
        self.date = date
        self.url = url
        self.source_name = source_name
        self.description = description
        self.channel = channel


# ---------------------------------------------------------------------------
# Markdown writer (YAML frontmatter)
# ---------------------------------------------------------------------------


def _write_markdown(
    item: ScrapedItem,
    src: SourceConfig,
    output_dir: Path,
) -> Path:
    """Write a ScrapedItem to disk as YAML-frontmatter markdown.

    File format:
      ---
      title: ...
      source: ...
      url: ...
      published: ...
      tags: [...]
      perspective: ...
      ---

      <body content>

    Returns the Path written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_title = _sanitize_filename(item.title)
    filename = f"{item.date} - {clean_title}.md" if item.date else f"{clean_title}.md"
    filepath = output_dir / filename

    post = frontmatter.Post(
        content=item.content,
        title=item.title,
        source=item.source_name,
        url=item.url,
        published=item.date,
        tags=list(src.tags),
        perspective=src.perspective or "",
    )
    filepath.write_text(frontmatter.dumps(post))
    return filepath


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def _run_rss(
    src: SourceConfig,
    fetch_feed: Callable,
    fetch_article: Callable,
) -> list[Path]:
    """RSS adapter: fetch feed, parse items, filter, dedup, write."""
    parsed = fetch_feed(src.feed_url)
    output_dir = Path(src.output_dir)
    written: list[Path] = []

    entries = getattr(parsed, "entries", []) or []
    limit = src.scrape_limit

    for entry in entries[:limit]:
        title = _strip_html(getattr(entry, "title", "") or "")
        link = getattr(entry, "link", "") or ""
        pub_date = ""
        # feedparser normalises pubDate into entry.published
        for attr in ("published", "updated", "pubDate"):
            pub_date = getattr(entry, attr, "") or ""
            if pub_date:
                break
        description = _strip_html(getattr(entry, "summary", "") or "")

        # Try content:encoded first, then summary
        content_list = getattr(entry, "content", None)
        if content_list:
            content = _strip_html(content_list[0].get("value", ""))
        else:
            content = description

        date_str = _parse_rss_date(pub_date)

        # Apply keyword filter against title + description (cheap, before fetch)
        if src.keyword_filter:
            combined = f"{title} {description}"
            if not _matches_keyword_filter(combined, src.keyword_filter):
                continue

        # Fetch full article body if configured
        if src.scrape_full_content and link:
            fetched = fetch_article(link)
            if fetched and len(fetched.split()) >= 100:
                content = fetched

        # Dedup
        clean_title = _sanitize_filename(title)
        if _file_exists_check(output_dir, date_str, clean_title):
            continue

        item = ScrapedItem(
            title=title,
            content=content,
            date=date_str,
            url=link,
            source_name=src.name,
            description=description,
        )
        path = _write_markdown(item, src, output_dir)
        written.append(path)

    return written


def _run_youtube(
    src: SourceConfig,
    fetch_youtube: Callable,
) -> list[Path]:
    """YouTube adapter: fetch captions list, filter, dedup, write."""
    output_dir = Path(src.output_dir)
    captions = fetch_youtube(src.feed_url, src.scrape_limit)
    written: list[Path] = []

    for cap in captions:
        title = cap.get("title", "")
        url = cap.get("url", "")
        published = cap.get("published", datetime.now().strftime("%Y-%m-%d"))
        transcript = cap.get("transcript", "")

        # Apply keyword filter
        if src.keyword_filter:
            combined = f"{title} {transcript}"
            if not _matches_keyword_filter(combined, src.keyword_filter):
                continue

        # Dedup
        clean_title = _sanitize_filename(title)
        if _file_exists_check(output_dir, published, clean_title):
            continue

        item = ScrapedItem(
            title=title,
            content=transcript,
            date=published,
            url=url,
            source_name=src.name,
        )
        path = _write_markdown(item, src, output_dir)
        written.append(path)

    return written


def _run_listing(
    src: SourceConfig,
    fetch_listing: Callable,
    fetch_article: Callable,
) -> list[Path]:
    """Listing adapter: scrape index page, extract article links, fetch each."""
    output_dir = Path(src.output_dir)
    listing_html = fetch_listing(src.feed_url)
    if not listing_html:
        logger.warning("listing adapter: no HTML returned for %s", src.feed_url)
        return []

    # Extract article links: /research/... or /news/...
    link_pattern = r'href="(/(?:research|news)/[a-zA-Z0-9_-]+)"'
    raw_links = re.findall(link_pattern, listing_html)
    seen: set[str] = set()
    links: list[str] = []
    for lnk in raw_links:
        if lnk not in seen:
            seen.add(lnk)
            links.append(lnk)

    written: list[Path] = []

    for link in links[: src.scrape_limit]:
        full_url = urljoin(src.feed_url, link)
        try:
            content = fetch_article(full_url)
            if not content or len(content.split()) < 100:
                continue
            # Extract title from first markdown heading, or derive from URL
            title = "Untitled"
            for line in content.split("\n")[:10]:
                stripped = line.strip()
                if stripped.startswith("# "):
                    title = stripped[2:].strip()
                    break
            if title == "Untitled":
                path_part = full_url.rstrip("/").split("/")[-1]
                if path_part:
                    title = path_part.replace("-", " ").replace("_", " ").title()

            # Attempt to find a date in the first 500 chars
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", content[:500])
            date_str = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

            # Apply keyword filter
            if src.keyword_filter:
                combined = f"{title} {content[:1000]}"
                if not _matches_keyword_filter(combined, src.keyword_filter):
                    continue

            # Dedup
            clean_title = _sanitize_filename(title)
            if _file_exists_check(output_dir, date_str, clean_title):
                continue

            item = ScrapedItem(
                title=title,
                content=content,
                date=date_str,
                url=full_url,
                source_name=src.name,
            )
            path = _write_markdown(item, src, output_dir)
            written.append(path)
        except Exception as exc:
            logger.warning("listing adapter: failed to fetch %s: %s", full_url, exc)
            continue

    return written


# ---------------------------------------------------------------------------
# Default real-fetch callables (import lazily to keep module importable
# even when optional deps are absent)
# ---------------------------------------------------------------------------


def _default_fetch_feed(url: str):  # type: ignore[return]
    """Default feed fetcher: HTTP GET + feedparser."""
    import urllib.request

    import feedparser

    with urllib.request.urlopen(url, timeout=15) as resp:
        xml_text = resp.read().decode("utf-8", errors="replace")
    return feedparser.parse(xml_text)


def _default_fetch_article(url: str) -> Optional[str]:
    """Default article fetcher: direct HTTP + trafilatura."""
    from core.fetch import fetch_article_direct

    return fetch_article_direct(url)


def _default_fetch_youtube(channel_url: str, limit: int) -> list[dict]:
    """Default YouTube captions fetcher (requires yt-dlp + youtube-transcript-api)."""
    try:
        from youtube_transcripts import get_channel_videos, process_video  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "YouTube deps not installed. Run: uv sync  (yt-dlp required)"
        ) from exc

    videos = get_channel_videos(channel_url, limit)
    return [
        {
            "title": v.get("title", v["id"]),
            "url": v["url"],
            "published": v.get("upload_date", ""),
            "transcript": "",
        }
        for v in videos
    ]


def _default_fetch_listing(url: str) -> Optional[str]:
    """Default listing page fetcher: headless browser HTML."""
    from core.fetch import fetch_html_with_browser

    return fetch_html_with_browser(url)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_source(
    src: SourceConfig,
    *,
    fetch_feed: Optional[Callable] = None,
    fetch_article: Optional[Callable] = None,
    fetch_youtube: Optional[Callable] = None,
    fetch_listing: Optional[Callable] = None,
) -> list[Path]:
    """Scrape *src* and return a list of Paths of files written.

    Injectable callables (keyword-only, all default to real network functions):
      fetch_feed(url)              → feedparser.FeedParserDict
      fetch_article(url)           → str | None
      fetch_youtube(channel_url, limit) → list[dict]  (title, url, published, transcript)
      fetch_listing(url)           → str | None   (raw HTML of index page)

    Pass lambdas / stubs in tests to avoid network I/O.
    """
    _feed = fetch_feed or _default_fetch_feed
    _article = fetch_article or _default_fetch_article
    _youtube = fetch_youtube or _default_fetch_youtube
    _listing = fetch_listing or _default_fetch_listing

    if src.type == "rss":
        return _run_rss(src, _feed, _article)
    elif src.type == "youtube":
        return _run_youtube(src, _youtube)
    elif src.type == "listing":
        return _run_listing(src, _listing, _article)
    else:
        raise ValueError(
            f"run_source: unsupported source type '{src.type}'. "
            f"v1 supports: rss, youtube, listing."
        )
