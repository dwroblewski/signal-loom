"""core/brief.py — Build a grouped markdown digest from the signal-loom index.

Public API
----------
build(index_path, *, since, until, verify, limit) -> str
    Build a markdown digest grouped by controlled-vocabulary primary topic.

last_verification() -> dict[str, str]
    Return the URL→tier map from the most recent build(..., verify=True) call.

Verification tiers
------------------
- "live"   : 2xx or 3xx HTTP status
- "dead"   : 404 or 410 HTTP status
- "stale"  : network error, timeout, 5xx, or any other non-dead failure
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from core import query
from core.fetch import BlockedURLError
from core import fetch as _fetch_mod

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {"verification": {}}

_HEAD_TIMEOUT = 8.0  # seconds


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _classify_response(status_code: int) -> str:
    """Map an HTTP status code to a verification tier."""
    if status_code in (404, 410):
        return "dead"
    if 200 <= status_code < 400:
        return "live"
    return "stale"


def _head_check(urls: list[str]) -> dict[str, str]:
    """HEAD-check each URL and return a url→tier mapping.

    - 2xx/3xx → "live"
    - 404/410  → "dead"
    - network error, timeout, 5xx → "stale"
    """
    results: dict[str, str] = {}
    # follow_redirects=False: a 3xx is treated as live per tiering logic below;
    # we must NOT chase redirects into private address space (SSRF).
    with httpx.Client(timeout=_HEAD_TIMEOUT, follow_redirects=False) as client:
        for url in urls:
            if not url:
                continue
            # SSRF guard: reject private/internal URLs before issuing any request.
            try:
                _fetch_mod._assert_safe_url(url)
            except BlockedURLError:
                results[url] = "blocked"
                continue
            try:
                resp = client.head(url)
                results[url] = _classify_response(resp.status_code)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError):
                results[url] = "stale"
    return results


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


def _group_by_primary_topic(
    entries: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group entries by each value in topics.primary.

    An entry with N primary topics appears under all N groups.
    Entries with no primary topics land in "(uncategorized)".
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        primaries = (entry.get("topics") or {}).get("primary") or []
        if primaries:
            for topic in primaries:
                groups[topic].append(entry)
        else:
            groups["(uncategorized)"].append(entry)
    return dict(groups)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_TIER_ICON = {
    "live": "✓ live",
    "stale": "⚠ stale",
    "dead": "✗ dead",
    "blocked": "✗ blocked",
}


def _md_escape(text: str) -> str:
    """Escape markdown link/code/HTML metacharacters in untrusted scraped text.

    Titles and summaries come verbatim from remote feeds / model output. Without
    escaping:
      * ``x](https://evil/phish) [`` breaks out of a ``[text](url)`` link and
        forges a second one — escape ``[``/``]``;
      * a backtick opens a code span — escape `` ` ``;
      * ``<https://evil>`` is a CommonMark autolink (a first-class link needing
        no brackets) and ``<a href=…>`` is raw HTML — escape ``<``/``>`` so
        neither renders as an active link, closing the bypass around _safe_url.
    Parens in plain text are harmless (the URL uses an angle-bracket destination).
    """
    for ch in ("\\", "`", "[", "]", "<", ">"):
        text = text.replace(ch, "\\" + ch)
    return text


def _safe_url(url: str) -> str | None:
    """Return *url* rendered for a markdown angle-bracket destination, or None.

    Drops ``javascript:``/``data:``/other non-http(s) schemes (feed ``link``
    values are attacker-controlled). The result is meant to be wrapped in
    ``[text](<...>)`` — the angle-bracket form lets the URL contain parentheses
    (Wikipedia disambiguation links, etc.) without the ``)``-encoding that would
    break a bare destination. Only ``<``, ``>`` and whitespace need neutralizing.
    Scheme-relative/relative URLs are allowed through.
    """
    scheme = (urlparse(url).scheme or "").lower()
    if scheme and scheme not in ("http", "https"):
        return None
    return (
        url.replace("<", "%3C")
        .replace(">", "%3E")
        .replace(" ", "%20")
        .replace("\n", "%0A")
        .replace("\r", "%0D")
    )


def _render(
    entries: list[dict[str, Any]],
    *,
    since: str | None,
    until: str | None,
    verification: dict[str, str] | None,
) -> str:
    """Render a grouped markdown digest string."""
    groups = _group_by_primary_topic(entries)

    # Sort groups: larger groups first, then alphabetically for determinism
    sorted_groups = sorted(
        groups.items(),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )

    lines: list[str] = []

    # Title / date-range header
    date_range = ""
    if since and until:
        date_range = f" · {since} – {until}"
    elif since:
        date_range = f" · since {since}"
    elif until:
        date_range = f" · until {until}"
    lines.append(f"# Signal Brief{date_range}")
    lines.append(f"_Generated {date.today().isoformat()} · {len(entries)} entries_")
    lines.append("")

    for topic, group_entries in sorted_groups:
        lines.append(f"## {topic}")
        for e in group_entries:
            title = e.get("title") or "(untitled)"
            url = e.get("url") or ""
            src = e.get("source") or ""
            pub = e.get("published") or ""
            summary = e.get("summary") or ""

            # Truncate summary to ~120 chars for a snippet. Escape it too — the
            # summary is untrusted model output over scraped text, so it can
            # carry the same [x](javascript:…) link injection the title escaping
            # closes. (Escape AFTER truncation so we never split a backslash.)
            snippet = _md_escape(summary[:120].rstrip())
            if len(summary) > 120:
                snippet += "…"

            tier_annotation = ""
            if verification is not None and url:
                tier = verification.get(url, "stale")
                tier_annotation = f" · {_TIER_ICON.get(tier, tier)}"

            safe_title = _md_escape(title)
            safe_url = _safe_url(url) if url else None
            # Angle-bracket destination so URLs with parens render as valid links.
            link_part = f"[{safe_title}](<{safe_url}>)" if safe_url else safe_title
            meta = " · ".join(filter(None, [src, pub]))
            bullet = f"- {link_part}{tier_annotation} — {meta}"
            if snippet:
                bullet += f"\n  _{snippet}_"
            lines.append(bullet)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build(
    index_path: str | Path,
    *,
    since: str | None = None,
    until: str | None = None,
    verify: bool = False,
    limit: int = 50,
) -> str:
    """Build a grouped markdown digest from the signal-loom index.

    Parameters
    ----------
    index_path:
        Path to ``index.json``.
    since:
        ISO date lower bound passed to ``query.window``.
    until:
        ISO date upper bound passed to ``query.window``.
    verify:
        When True, HEAD-check every unique URL and annotate each link
        with its verification tier (live/stale/dead).
    limit:
        Maximum entries to include (passed to ``query.window``).

    Returns
    -------
    str
        Rendered markdown digest.
    """
    entries = query.window(index_path, since=since, until=until, limit=limit)

    verification: dict[str, str] | None = None
    if verify:
        unique_urls = list(
            dict.fromkeys(e["url"] for e in entries if e.get("url"))
        )
        verification = _head_check(unique_urls)
        _state["verification"] = verification
    else:
        # Do not reset state so last_verification() still returns prior results
        pass

    return _render(entries, since=since, until=until, verification=verification)


def last_verification() -> dict[str, str]:
    """Return the URL→tier map from the most recent build(..., verify=True) call.

    Returns an empty dict if no verify=True build has been run in this session.
    """
    return dict(_state.get("verification", {}))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_date(value: str) -> str:
    """Accept 'Nd' (N days ago) or an ISO date; return an ISO date string."""
    if value.endswith("d") and value[:-1].isdigit():
        days = int(value[:-1])
        return (date.today() - timedelta(days=days)).isoformat()
    return value  # assume ISO date, let query.window validate


def main(argv: list[str] | None = None) -> int:
    """CLI: python -m core.brief [options]"""
    from core.config import (
        ConfigNotFoundError,
        load_settings,
        resolve_config_path,
    )

    parser = argparse.ArgumentParser(
        prog="core.brief",
        description="Build a grouped markdown digest from the signal-loom index.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to signal-loom.yaml (default: auto-discovered)",
    )
    parser.add_argument(
        "--index",
        default=None,
        help="Path to index.json (default: resolved from config)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Start date: ISO date (2026-05-01) or relative like 7d",
    )
    parser.add_argument(
        "--until",
        default=None,
        help="End date: ISO date",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        default=False,
        help="HEAD-check each URL and annotate with live/stale/dead",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of entries to include (default: 50)",
    )
    args = parser.parse_args(argv)

    # Resolve the index path: --index flag > config-derived > fallback "index.json"
    index_path_str = args.index
    if index_path_str is None:
        try:
            config_path = resolve_config_path(args.config)
            settings = load_settings(config_path)
            index_path_str = settings.index_path
        except ConfigNotFoundError:
            # `brief` is a query, not a generator — fall back to "index.json" in
            # cwd so users can ad-hoc inspect indexes without a project config.
            index_path_str = "index.json"
        except Exception as exc:
            # A config EXISTS but failed to load (YAML syntax error, bad value).
            # Surface it loudly — silently reading ./index.json would serve a
            # stale root-level index and hide the real problem.
            print(f"error loading config: {exc}", file=sys.stderr)
            return 1

    # Friendly error if the index file doesn't exist
    index_path = Path(index_path_str)
    if not index_path.exists():
        print(
            f"index.json not found at {index_path} — "
            f"run the pipeline first (/pipeline or python -m core.pipeline)",
            file=sys.stderr,
        )
        return 1

    since = _parse_date(args.since) if args.since else None
    until = _parse_date(args.until) if args.until else None
    try:
        md = build(
            index_path_str,
            since=since,
            until=until,
            verify=args.verify,
            limit=args.limit,
        )
    except FileNotFoundError:
        print(
            f"index.json not found at {index_path_str} — "
            f"run the pipeline first (/pipeline or python -m core.pipeline)",
            file=sys.stderr,
        )
        return 1

    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
