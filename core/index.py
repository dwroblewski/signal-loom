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
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

import frontmatter  # python-frontmatter

logger = logging.getLogger(__name__)


def _ensure_list(val: object) -> list:
    """Return *val* as a list, coercing str → [str] and None → [].

    Type-defensive: non-string iterables that are not lists (e.g. int, dict,
    None) are returned as [] rather than crashing the index build.
    A bare string is wrapped in a single-element list.
    """
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    if isinstance(val, (int, float, bool, dict)):
        # Non-iterable scalar or dict — cannot meaningfully coerce to list.
        return []
    try:
        return list(val)
    except TypeError:
        return []


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
        "enriched": True,
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
        try:
            entry = _build_entry(md_path, content_dir)
        except Exception as exc:
            logger.warning("index: skipping malformed file %s: %s", md_path, exc)
            entry = None
        if entry is not None:
            entries.append(entry)

    # Sort by published descending; files with no date sort last (stable)
    entries.sort(key=lambda e: e["published"] or "", reverse=True)

    result: dict = {
        "entries": entries,
        "generated": datetime.now(timezone.utc).isoformat(),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic write: write to temp file in same directory, then os.replace.
    tmp_path_str: str | None = None
    try:
        fd, tmp_path_str = tempfile.mkstemp(dir=out_path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(result, indent=2))
        os.replace(tmp_path_str, out_path)
        tmp_path_str = None  # replaced successfully — no cleanup needed
    finally:
        if tmp_path_str is not None:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``python -m core.index``.

    Loads settings from ``--config`` (default ``config/signal-loom.yaml``),
    calls :func:`build_index`, and prints a summary line to stdout.

    Exits 0 on success, 1 on error.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m core.index",
        description="Build index.json from enriched markdown files.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to signal-loom.yaml (default: auto-discovered)",
    )
    args = parser.parse_args(argv)

    try:
        from core.config import ensure_configs, load_settings, resolve_config_path

        config_path = resolve_config_path(args.config)
        ensure_configs(config_path.parent)

        if not config_path.exists():
            print(
                f"config not found at {config_path}; "
                f"copy config/signal-loom.example.yaml → config/signal-loom.yaml",
                file=sys.stderr,
            )
            return 1

        settings = load_settings(config_path)
        result = build_index(settings.content_dir, settings.index_path)
        n = len(result["entries"])
        print(f"index: {n} entries → {settings.index_path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
