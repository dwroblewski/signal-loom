"""Windowed reads of index.json so consumers never load the whole index."""

import json
from pathlib import Path
from typing import Any


def window(
    index_path: str | Path,
    *,
    since: str | None = None,
    until: str | None = None,
    topic: str | None = None,
    source: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return a filtered, sorted, capped slice of index entries.

    Parameters
    ----------
    index_path:
        Path to the ``index.json`` file produced by ``core.index``.
    since:
        Inclusive lower bound on ``published`` (ISO date string, e.g. ``"2026-05-21"``).
    until:
        Inclusive upper bound on ``published``.
    topic:
        Keep only entries where *topic* appears in ``topics.primary`` OR
        ``topics.secondary``.
    source:
        Keep only entries whose ``source`` field matches exactly.
    limit:
        Maximum number of entries to return (default 50).

    Returns
    -------
    list[dict]
        Entries sorted newest-first, capped at *limit*.  Empty list when
        nothing matches.  Never returns the whole index dict.
    """
    data = json.loads(Path(index_path).read_text())
    entries: list[dict[str, Any]] = data.get("entries", [])

    def keep(e: dict[str, Any]) -> bool:
        p = e.get("published") or ""
        if since and p < since:
            return False
        if until and p > until:
            return False
        if topic:
            topics = e.get("topics", {})
            all_topics = topics.get("primary", []) + topics.get("secondary", [])
            if topic not in all_topics:
                return False
        if source and e.get("source") != source:
            return False
        return True

    out = [e for e in entries if keep(e)]
    out.sort(key=lambda e: e.get("published") or "", reverse=True)
    return out[:limit]
