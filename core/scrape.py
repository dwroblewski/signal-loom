"""core/scrape.py — Source adapters: RSS, YouTube, and listing page scrapers.

Adapted from an earlier content pipeline.

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

import calendar
import hashlib
import html as _html_module
import logging
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin

import frontmatter

from core.config import SourceConfig

logger = logging.getLogger(__name__)

_RATE_LIMIT_RETRY_FALLBACK_SECONDS = 65.0
_RATE_LIMIT_RETRY_MAX_SECONDS = 300.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_filename(title: str, max_len: int = 100) -> str:
    """Convert title to safe filename with clean formatting."""
    title = _html_module.unescape(title)
    # Strip null bytes (would create corrupted or unreadable filenames)
    title = title.replace("\x00", "")
    # Strip YouTube hashtags (#ai #llm etc.)
    title = re.sub(r"\s*#\w+", "", title)
    # Normalize em/en dashes → regular hyphen with spaces
    title = re.sub(r"[—–]", " - ", title)
    # Normalize curly quotes
    title = title.replace("'", "'").replace("'", "'")
    title = title.replace(""", "").replace(""", "")
    # Remove problematic filesystem characters
    title = re.sub(r'[<>:"/\\|?*]', "", title)
    # Collapse whitespace
    title = re.sub(r"\s+", " ", title).strip()
    # Strip leading dots and whitespace (prevent hidden files like ".hidden")
    title = title.lstrip(". ")
    # Truncate at word boundary
    if len(title) > max_len:
        title = title[:max_len].rsplit(" ", 1)[0]
    # Final safety: if result is empty after stripping, use a placeholder
    if not title:
        title = "untitled"
    return title


def _url_hash(url: str, length: int = 6) -> str:
    """Return a short hex hash of the URL for filename disambiguation."""
    return hashlib.sha1(url.encode()).hexdigest()[:length]


def _rate_limit_retry_seconds(retry_after: str | None) -> float:
    """Return a bounded 429 retry delay from Retry-After seconds or HTTP-date."""
    if not retry_after:
        return _RATE_LIMIT_RETRY_FALLBACK_SECONDS
    try:
        seconds = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = _RATE_LIMIT_RETRY_FALLBACK_SECONDS
    seconds = max(0.0, seconds)
    return min(seconds, _RATE_LIMIT_RETRY_MAX_SECONDS)


def _parse_rss_date(
    pub_date: str,
    entry: Any = None,
) -> Optional[str]:
    """Parse an RSS/Atom pubDate or ISO 8601 date string into ISO date (YYYY-MM-DD).

    Resolution order:
      1. feedparser's entry.published_parsed / entry.updated_parsed
         (a time.struct_time) → calendar.timegm → utcfromtimestamp → strftime
      2. RFC 2822 string via email.utils.parsedate_to_datetime
      3. ISO 8601 string via dateutil.parser
      4. Fall back to datetime.now().strftime("%Y-%m-%d")
    """
    # 1. Prefer feedparser's already-parsed struct_time (most accurate for Atom)
    if entry is not None:
        for attr in ("published_parsed", "updated_parsed"):
            ts = getattr(entry, attr, None)
            if ts is not None:
                try:
                    return datetime.fromtimestamp(calendar.timegm(ts), tz=timezone.utc).strftime("%Y-%m-%d")
                except Exception:
                    pass

    if not pub_date:
        return datetime.now().strftime("%Y-%m-%d")

    # 2. RFC 2822 (standard RSS pubDate format)
    try:
        cleaned = pub_date.strip()
        if re.match(r"^[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4}$", cleaned):
            cleaned += " 00:00:00 GMT"
        dt = parsedate_to_datetime(cleaned)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    # 3. ISO 8601 / arbitrary date strings (Atom <published>, plain dates)
    try:
        from dateutil import parser as dateutil_parser
        dt = dateutil_parser.parse(pub_date.strip())
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return datetime.now().strftime("%Y-%m-%d")


def _file_exists_check(output_dir: Path, date_str: str, filename_stem: str) -> bool:
    """Return True if a file matching the exact filename stem already exists.

    ``filename_stem`` is the full sanitized title (including any URL hash suffix)
    that will be used verbatim in the filename.  We match on the exact prefix
    ``"<date> - <stem>"`` so two items that happen to share a long title prefix
    but have different URL hashes are NOT considered duplicates of each other.
    """
    prefix = f"{date_str} - {filename_stem}"
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
    filename_stem: Optional[str] = None,
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

    ``filename_stem`` is the sanitized title (with any disambiguation hash)
    to use verbatim in the filename.  If None, _sanitize_filename(item.title)
    is used.

    Returns the Path written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = filename_stem if filename_stem is not None else _sanitize_filename(item.title)
    filename = f"{item.date} - {stem}.md" if item.date else f"{stem}.md"
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

        # Pass the feedparser entry so _parse_rss_date can use *_parsed struct_time
        date_str = _parse_rss_date(pub_date, entry=entry)

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

        # Build filename stem: sanitize + append URL hash to avoid truncation collisions
        clean_title = _sanitize_filename(title)
        url_suffix = f"-{_url_hash(link)}" if link else ""
        filename_stem = f"{clean_title}{url_suffix}"

        if _file_exists_check(output_dir, date_str, filename_stem):
            continue

        item = ScrapedItem(
            title=title,
            content=content,
            date=date_str,
            url=link,
            source_name=src.name,
            description=description,
        )
        path = _write_markdown(item, src, output_dir, filename_stem=filename_stem)
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

        # Skip hollow entries with no transcript text
        if not transcript or not transcript.strip():
            logger.warning("youtube adapter: skipping '%s' — empty transcript", title)
            continue

        # Apply keyword filter
        if src.keyword_filter:
            combined = f"{title} {transcript}"
            if not _matches_keyword_filter(combined, src.keyword_filter):
                continue

        # Build filename stem with URL hash to disambiguate truncated titles
        clean_title = _sanitize_filename(title)
        url_suffix = f"-{_url_hash(url)}" if url else ""
        filename_stem = f"{clean_title}{url_suffix}"

        if _file_exists_check(output_dir, published, filename_stem):
            continue

        item = ScrapedItem(
            title=title,
            content=transcript,
            date=published,
            url=url,
            source_name=src.name,
        )
        path = _write_markdown(item, src, output_dir, filename_stem=filename_stem)
        written.append(path)

    return written


# Default broad link pattern for listing pages.
# Matches paths that look like article slugs: at least 8 chars, no query string,
# leading slash, contains only URL-safe chars.
_DEFAULT_LISTING_LINK_PATTERN = r'href="(/[a-z0-9][a-z0-9/_-]{8,})"'


def _run_listing(
    src: SourceConfig,
    fetch_listing: Callable,
    fetch_article: Callable,
) -> list[Path]:
    """Listing adapter: scrape index page, extract article links, fetch each.

    Keyword filtering is applied to the URL slug BEFORE fetch_article is called
    so we don't waste fetches on items that can't possibly match.
    """
    output_dir = Path(src.output_dir)
    listing_html = fetch_listing(src.feed_url)
    if not listing_html:
        logger.warning("listing adapter: no HTML returned for %s", src.feed_url)
        return []

    # Use source's custom pattern if provided, otherwise broad heuristic default
    link_pattern = (
        src.listing_link_pattern
        if src.listing_link_pattern is not None
        else _DEFAULT_LISTING_LINK_PATTERN
    )
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

        # Derive a title-candidate from the URL slug for pre-fetch filtering
        slug_title = link.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")

        # Apply keyword filter against slug BEFORE fetching full article
        if src.keyword_filter:
            if not _matches_keyword_filter(slug_title, src.keyword_filter):
                continue

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

            # Apply keyword filter against full content if it passes slug check
            if src.keyword_filter:
                combined = f"{title} {content[:1000]}"
                if not _matches_keyword_filter(combined, src.keyword_filter):
                    continue

            # Build filename stem with URL hash disambiguation
            clean_title = _sanitize_filename(title)
            url_suffix = f"-{_url_hash(full_url)}"
            filename_stem = f"{clean_title}{url_suffix}"

            if _file_exists_check(output_dir, date_str, filename_stem):
                continue

            item = ScrapedItem(
                title=title,
                content=content,
                date=date_str,
                url=full_url,
                source_name=src.name,
            )
            path = _write_markdown(item, src, output_dir, filename_stem=filename_stem)
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
    """Default feed fetcher: HTTP GET via httpx (certifi CA bundle) + parse.

    Uses httpx rather than ``urllib.request.urlopen`` — urllib relies on the
    interpreter's OpenSSL trust store, which is frequently empty on macOS
    Python builds and raises ``CERTIFICATE_VERIFY_FAILED``. httpx ships with
    certifi, matching the rest of the fetch layer (``core.fetch``).

    Applies the SSRF egress guard before the initial request and on every
    redirect hop (manual following, max 5 hops). Caps response at 10 MB.
    """
    import httpx

    from core import fetch

    try:
        fetch._assert_safe_url(url)
    except fetch.BlockedURLError as exc:
        logger.warning("_default_fetch_feed blocked URL %s: %s", url, exc)
        raise

    with httpx.Client(follow_redirects=False, timeout=20, headers={"User-Agent": "Mozilla/5.0 (signal-loom)"}) as client:
        current_url = url
        rate_limit_retries = 1
        for _ in range(fetch._MAX_REDIRECTS + 1 + rate_limit_retries):
            resp = client.get(current_url)
            if resp.is_redirect:
                location = resp.headers.get("location", "")
                if not location:
                    break
                next_url = urljoin(current_url, location)
                try:
                    fetch._assert_safe_url(next_url)
                except fetch.BlockedURLError as exc:
                    logger.warning("_default_fetch_feed blocked redirect to %s: %s", next_url, exc)
                    raise
                current_url = next_url
                continue
            if resp.status_code == 429 and rate_limit_retries > 0:
                wait_seconds = _rate_limit_retry_seconds(resp.headers.get("retry-after"))
                rate_limit_retries -= 1
                logger.warning(
                    "rate limited fetching feed %s; retrying after %.1fs",
                    current_url,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                continue
            resp.raise_for_status()
            if len(resp.content) > fetch._MAX_RESPONSE_BYTES:
                raise ValueError(
                    f"Feed response for {url} exceeds 10 MB ({len(resp.content)} bytes) — skipping."
                )
            return fetch.parse_feed(resp.text)
        raise ValueError(f"Too many redirects fetching feed {url}")


def _default_fetch_article(url: str) -> Optional[str]:
    """Default article fetcher: direct HTTP + trafilatura."""
    from core.fetch import fetch_article_direct

    return fetch_article_direct(url)


def _default_fetch_youtube(channel_url: str, limit: int) -> list[dict]:
    """Default YouTube captions fetcher using yt-dlp + youtube-transcript-api.

    Adapted from an earlier content pipeline:
      - yt-dlp lists videos from the channel (flat-playlist, dump-json)
      - youtube-transcript-api fetches captions per video ID
      - Prefer manual EN transcripts; fall back to auto-generated; fall back
        to any available language
      - On extraction failure for a video, log a warning and SKIP that video
        (do not emit a hollow entry with transcript: "")
    """
    import json
    import re
    import subprocess

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            NoTranscriptFound,
            TranscriptsDisabled,
            VideoUnavailable,
        )
    except ImportError as exc:
        raise RuntimeError(
            "YouTube deps not installed. Run: uv sync  (yt-dlp and youtube-transcript-api required)"
        ) from exc

    # Guard: only allow known YouTube hostnames over HTTPS to prevent SSRF via
    # arbitrary feed_url values being passed to yt-dlp as a shell subprocess.
    _ALLOWED_YT_HOSTS = frozenset({
        "www.youtube.com",
        "youtube.com",
        "m.youtube.com",
        "youtu.be",
    })
    from urllib.parse import urlparse as _urlparse
    _parsed_yt = _urlparse(channel_url)
    if _parsed_yt.scheme != "https" or (_parsed_yt.netloc or "").lower() not in _ALLOWED_YT_HOSTS:
        logger.error(
            "youtube adapter: feed_url '%s' is not a valid YouTube URL "
            "(must be https:// on youtube.com/youtu.be) — skipping source.",
            channel_url,
        )
        return []

    # 1. List videos from channel using yt-dlp
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--playlist-end", str(limit),
        channel_url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "yt-dlp not found. Install with: uv sync  (yt-dlp required)"
        ) from exc
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp timed out fetching channel: %s", channel_url)
        return []

    if result.returncode != 0:
        logger.warning("yt-dlp returned non-zero for %s: %s", channel_url, result.stderr[:300])
        return []

    videos: list[dict] = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = data.get("id")
        if not video_id:
            continue
        upload_date = data.get("upload_date", "")
        if upload_date and len(upload_date) == 8:
            upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
        videos.append({
            "id": video_id,
            "title": data.get("title", video_id),
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "upload_date": upload_date,
        })

    # 2. Fetch transcripts for each video
    results: list[dict] = []
    ytt_api = YouTubeTranscriptApi()

    for v in videos:
        video_id = v["id"]
        title = v["title"]
        url = v["url"]
        upload_date = v.get("upload_date", "")

        try:
            transcript_list = ytt_api.list(video_id)
            # Prefer manual EN, fall back to auto-generated EN, then any language
            fetched_transcript = None
            try:
                t = transcript_list.find_manually_created_transcript(["en"])
                fetched_transcript = list(t.fetch())
            except NoTranscriptFound:
                pass
            if fetched_transcript is None:
                try:
                    t = transcript_list.find_generated_transcript(["en"])
                    fetched_transcript = list(t.fetch())
                except NoTranscriptFound:
                    pass
            if fetched_transcript is None:
                for t in transcript_list:
                    fetched_transcript = list(t.fetch())
                    break

            if not fetched_transcript:
                logger.warning("youtube adapter: no transcript found for '%s' (%s)", title, video_id)
                continue

            # Concatenate transcript segments into plain text
            transcript_text = " ".join(
                seg.text.strip() if hasattr(seg, "text") else seg.get("text", "").strip()
                for seg in fetched_transcript
            )
            if not transcript_text.strip():
                logger.warning("youtube adapter: empty transcript for '%s' (%s)", title, video_id)
                continue

            results.append({
                "title": title,
                "url": url,
                "published": upload_date or datetime.now().strftime("%Y-%m-%d"),
                "transcript": transcript_text,
            })

        except (TranscriptsDisabled, VideoUnavailable) as exc:
            logger.warning("youtube adapter: skipping '%s' (%s): %s", title, video_id, exc)
            continue
        except Exception as exc:
            logger.warning("youtube adapter: error fetching transcript for '%s' (%s): %s", title, video_id, exc)
            continue

    return results


def _default_fetch_listing(url: str) -> Optional[str]:
    """Default listing page fetcher: headless browser HTML."""
    from core.fetch import fetch_html_with_browser

    return fetch_html_with_browser(url)


def _direct_fetch_listing(url: str) -> Optional[str]:
    """Listing page fetcher using direct httpx GET (no browser required).

    Used when fetch_method is 'auto' or unset — tries a direct HTTP request
    first (fast, no extra deps).  Returns raw HTML as a string, or None on
    failure.
    """
    import httpx
    from core import fetch as _fetch_mod

    try:
        _fetch_mod._assert_safe_url(url)
    except _fetch_mod.BlockedURLError as exc:
        logger.warning("_direct_fetch_listing blocked URL %s: %s", url, exc)
        raise

    try:
        with httpx.Client(
            follow_redirects=False,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (signal-loom)"},
        ) as client:
            current_url = url
            for _ in range(_fetch_mod._MAX_REDIRECTS + 1):
                resp = client.get(current_url)
                if resp.is_redirect:
                    location = resp.headers.get("location", "")
                    if not location:
                        break
                    next_url = urljoin(current_url, location)
                    _fetch_mod._assert_safe_url(next_url)
                    current_url = next_url
                    continue
                resp.raise_for_status()
                if len(resp.content) > _fetch_mod._MAX_RESPONSE_BYTES:
                    logger.warning(
                        "_direct_fetch_listing: response for %s exceeds 10 MB — skipping",
                        url,
                    )
                    return None
                return resp.text
            logger.warning("_direct_fetch_listing: too many redirects for %s", url)
            return None
    except Exception as exc:
        logger.debug("_direct_fetch_listing: failed for %s: %s", url, exc)
        return None


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

    # Resolve listing fetcher based on fetch_method:
    # - "browser"       → always use Playwright (requires --extra browser)
    # - "auto" / unset  → try direct httpx first; fall back to browser if empty
    # - "auto-no-browser" → direct httpx only, no browser fallback
    if fetch_listing is not None:
        _listing = fetch_listing
    elif src.fetch_method == "browser":
        _listing = _default_fetch_listing
    else:
        # "auto" or unset: try direct first, browser fallback (unless auto-no-browser)
        def _listing(url: str) -> Optional[str]:  # type: ignore[misc]
            html = _direct_fetch_listing(url)
            if html:
                return html
            if src.fetch_method == "auto-no-browser":
                return None
            # Fall back to browser
            return _default_fetch_listing(url)

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
