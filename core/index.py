"""core/index.py — Build index.json from an enriched markdown content directory.

Walks *content_dir* recursively for ``*.md`` files, skips any file that does
not carry ``enriched: true`` in its YAML frontmatter, and writes a compact
cross-source index to *out_path*.

Ported and simplified from the vault's build_index.py (AI Signals skill).
Vault-specific concerns (hub paths, entity canonicalisation, scoring, claims)
are intentionally stripped; this module only handles corpus walk + entry
construction.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

import frontmatter  # python-frontmatter


def _ensure_list(val: object) -> list:
    """Return *val* as a list, coercing str → [str] and None → []."""
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    return list(val)


def _build_entry(md_path: Path, content_dir: Path) -> dict | None:
    """Parse *md_path* and return an index entry, or None if not enriched.

    Args:
        md_path: Absolute path to the markdown file.
        content_dir: Root of the content tree (used to compute relative path).

    Returns:
        An entry dict, or ``None`` if the file lacks ``enriched: true``.
    """
    try:
        post = frontmatter.load(str(md_path))
    except Exception:
        return None

    fm = post.metadata

    # Only include files explicitly marked as enriched
    if fm.get("enriched") is not True:
        return None

    # topics
    raw_topics = fm.get("topics") or {}
    if not isinstance(raw_topics, dict):
        raw_topics = {}
    topics = {
        "primary": _ensure_list(raw_topics.get("primary")),
        "secondary": _ensure_list(raw_topics.get("secondary")),
    }

    # entities
    raw_entities = fm.get("entities") or {}
    entities: dict[str, list] = {}
    if isinstance(raw_entities, dict):
        for cat, val in raw_entities.items():
            entities[cat] = _ensure_list(val)

    entry: dict = {
        "path": md_path.relative_to(content_dir).as_posix(),
        "title": fm.get("title") or md_path.stem,
        "source": fm.get("source") or "",
        "url": fm.get("url") or "",
        "published": str(fm.get("published") or ""),
        "tags": _ensure_list(fm.get("tags")),
        "topics": topics,
        "entities": entities,
        "summary": fm.get("summary") or "",
    }
    return entry


def build_index(
    content_dir: Union[Path, str],
    out_path: Union[Path, str],
) -> dict:
    """Build an index from all enriched markdown files in *content_dir*.

    Recursively finds every ``*.md`` file under *content_dir*, skips files
    without ``enriched: true``, builds one entry per file, sorts entries by
    ``published`` descending (stable), and writes the result as JSON to
    *out_path*.

    Args:
        content_dir: Directory tree containing enriched markdown files.
        out_path: Destination path for ``index.json``.

    Returns:
        The index dict that was written to *out_path*.
    """
    content_dir = Path(content_dir)
    out_path = Path(out_path)

    entries: list[dict] = []
    for md_path in sorted(content_dir.rglob("*.md")):
        entry = _build_entry(md_path, content_dir)
        if entry is not None:
            entries.append(entry)

    # Sort by published descending; files with no date sort last (stable)
    entries.sort(key=lambda e: e["published"] or "", reverse=True)

    result: dict = {
        "entries": entries,
        "generated": datetime.now(timezone.utc).isoformat(),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

    return result
