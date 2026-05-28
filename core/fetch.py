"""core/fetch.py — Network fetch layer for signal-loom.

Ported from internal-pipeline/scripts/pipeline_utils.py (fetch-related functions).

Provides:
  fetch_article_direct     — httpx + trafilatura, fast path (~500ms-3s p50)
  _fetch_browser_html      — headless Firefox via Playwright (browser extra)
  fetch_article_with_browser — direct HTTP first, browser fallback
  fetch_html_with_browser  — raw HTML via headless Firefox (for listing pages)
  strip_html               — HTML tag removal + entity decode
  classify_content         — 'full' / 'truncated' / 'stub' word-count classifier
  is_usable_content        — boolean gate over classify_content
  parse_feed               — thin feedparser.parse wrapper (pure, testable)

Stripped from source (intentionally not ported):
  - fetch_article_via_jina / Jina dependency — dropped entirely
  - emit_pipeline_telem, get_cycle_id, fetch_article_tracked,
    fetch_html_tracked — vault telemetry, not needed here
  - canonical_entities import, ENRICHMENT_PROMPT — belongs in enrich module
  - vault paths / VAULT_ROOT — no vault coupling in this module
  - fetch_rss_feed (network) — replaced by pure parse_feed for testability;
    network feed fetching belongs in scrape.py

Browser extra guard (differs from vault source):
  The vault silently falls back / silently skips when Playwright is missing.
  Here, if a browser fetch is requested but playwright is not installed,
  BrowserExtraMissing is raised with a message containing
  "uv sync --extra browser" so the caller gets an actionable error.
"""

import html as _html_module
import logging
import re
from typing import Optional

import feedparser
import httpx
import trafilatura

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Playwright availability guard — checked once at import time
# ---------------------------------------------------------------------------
try:
    import playwright  # noqa: F401  (presence check only)
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


class BrowserExtraMissing(RuntimeError):
    """Raised when a browser fetch is requested but playwright is not installed.

    Install with:  uv sync --extra browser
    """


# ---------------------------------------------------------------------------
# Browser constants (shared between _fetch_browser_html and callers)
# ---------------------------------------------------------------------------

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)
# 30s is plenty for domcontentloaded — pages needing longer were already broken
# under the old 60s networkidle setting in the vault pipeline.
_BROWSER_TIMEOUT_MS = 30_000
# Resource types that are never needed for article text extraction.
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
# Third-party analytics / tracking hosts that hold connections open and prevent
# networkidle — blocking them also speeds up headless fetches significantly.
_BLOCKED_URL_HOSTS = (
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "facebook.net", "hotjar.com", "segment.io", "segment.com",
    "amplitude.com", "mixpanel.com", "intercom.io", "branch.io",
    "fullstory.com", "newrelic.com", "cdn.optimizely.com",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_article_direct(url: str, timeout: int = 15) -> Optional[str]:
    """Fetch article via plain HTTP (httpx) and extract body with trafilatura.

    Fast path — ~500ms-3s p50, no browser subprocess. Works for
    server-rendered/SSG sites (most modern news/blogs). Returns None on
    network error, timeout, or if trafilatura yields no usable body — caller
    falls back to browser fetch.
    """
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers={"User-Agent": _BROWSER_UA}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
        text = trafilatura.extract(html, include_comments=False, include_tables=True)
        return text or None
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        logger.debug("fetch_article_direct network error for %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("fetch_article_direct unexpected error for %s: %s: %s", url, type(exc).__name__, exc)
        return None


def _fetch_browser_html(
    url: str,
    timeout_ms: int = _BROWSER_TIMEOUT_MS,
    retries: int = 1,
) -> tuple[Optional[str], Optional[str]]:
    """Fetch raw HTML via headless Firefox with hardened resilience.

    Returns (html, error_message). Exactly one of the two is always None.

    Raises BrowserExtraMissing if playwright is not installed.

    Hardening (ported from vault pipeline):
      - User-Agent matches the direct-HTTP path.
      - Blocks images/media/fonts and known analytics/tracker hosts.
      - wait_until="domcontentloaded" (not networkidle).
      - Browser close inside try/finally — no leaked subprocesses.
      - One automatic retry on TimeoutError.
    """
    if not _PLAYWRIGHT_AVAILABLE:
        raise BrowserExtraMissing(
            "playwright is not installed. Install with: uv sync --extra browser"
        )

    from playwright.sync_api import TimeoutError as PWTimeout, sync_playwright

    def _route(route):  # type: ignore[no-untyped-def]
        req = route.request
        if req.resource_type in _BLOCKED_RESOURCE_TYPES:
            return route.abort()
        host = req.url.split("/", 3)[2] if "://" in req.url else ""
        if any(blocked in host for blocked in _BLOCKED_URL_HOSTS):
            return route.abort()
        return route.continue_()

    last_err: Optional[str] = None
    for attempt in range(retries + 1):
        try:
            with sync_playwright() as p:
                browser = p.firefox.launch(headless=True)
                try:
                    context = browser.new_context(user_agent=_BROWSER_UA)
                    page = context.new_page()
                    page.route("**/*", _route)
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    return page.content(), None
                finally:
                    browser.close()
        except PWTimeout:
            last_err = f"timeout after {timeout_ms}ms (attempt {attempt + 1}/{retries + 1})"
            continue
        except Exception as exc:
            last_err = str(exc)[:200]
            break
    return None, last_err


def fetch_article_with_browser(url: str, timeout_ms: int = _BROWSER_TIMEOUT_MS) -> Optional[str]:
    """Fetch article text using direct HTTP first, falling back to headless Firefox.

    Strategy:
      1. Try direct httpx + trafilatura (~1s, works for most server-rendered/SSG sites).
      2. If direct yields nothing usable, fall back to hardened Playwright fetch.

    ``timeout_ms`` is in MILLISECONDS (Playwright convention).

    Raises BrowserExtraMissing if Playwright is not installed and the direct
    path either fails or returns insufficient content.
    """
    # Fast path: direct HTTP + trafilatura.
    direct_text = fetch_article_direct(url)
    if direct_text and is_usable_content(direct_text, min_body_words=100):
        return direct_text

    # Fallback: hardened Playwright (raises BrowserExtraMissing if not installed).
    html, err = _fetch_browser_html(url, timeout_ms=timeout_ms)
    if html is None:
        if err:
            logger.warning("fetch_article_with_browser browser fetch failed for %s: %s", url, err)
        return None
    extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
    return extracted or None


def fetch_html_with_browser(url: str, timeout_ms: int = _BROWSER_TIMEOUT_MS) -> Optional[str]:
    """Fetch JS-rendered page via headless Firefox, return raw HTML.

    For link extraction on listing pages (e.g. Anthropic's index). Use
    fetch_article_with_browser for article body text extraction.

    ``timeout_ms`` is in MILLISECONDS (Playwright convention).

    Raises BrowserExtraMissing if Playwright is not installed.
    """
    html, err = _fetch_browser_html(url, timeout_ms=timeout_ms)
    if html is None:
        if err:
            logger.warning("fetch_html_with_browser browser fetch failed for %s: %s", url, err)
        return None
    return html


def strip_html(html_content: Optional[str]) -> str:
    """Remove HTML tags and clean up text.

    Handles None/empty input gracefully (returns '').
    """
    if not html_content:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", html_content)
    # Decode HTML entities
    text = _html_module.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def classify_content(content: str, min_body_words: int = 200) -> str:
    """Classify content as 'full', 'truncated', or 'stub'.

    Uses total word count as a simple, robust proxy for content quality.
    Thresholds:
      full:      >= 500 words
      truncated: >= min_body_words (default 200) and < 500 words
      stub:      < min_body_words
    """
    words = len(content.split())
    if words >= 500:
        return "full"
    elif words >= min_body_words:
        return "truncated"
    else:
        return "stub"


def is_usable_content(content: str, min_body_words: int = 200) -> bool:
    """Return True if content has enough body text to be worth processing."""
    return classify_content(content, min_body_words) != "stub"


def parse_feed(xml_text: str) -> feedparser.FeedParserDict:
    """Parse RSS/Atom XML text with feedparser.

    Pure function — no network I/O. Network fetching of feeds belongs in
    scrape.py. Accepts the raw XML string and returns the feedparser result.
    """
    return feedparser.parse(xml_text)
