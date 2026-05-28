"""Shared enrichment writeback path.

Every step downstream of the LLM call is owned by this module so the two
enrichment sites (headless API and interactive Claude sub-agent) share one
code path and can never drift.

Public API
----------
- :func:`apply` — parse raw model response → validate → normalize → sanitize
  → merge into file frontmatter → atomic write.  Retries (with optional
  regenerator) on malformed output; never crashes on bad data.
- :func:`apply_batch` — iterate apply() over a ``{Path: raw}`` mapping;
  one failure never aborts the batch; writes a ``failed-enrichments.jsonl``
  re-run queue.

Both functions are the ONLY place validation / normalization / sanitization /
frontmatter writes happen for enrichment output.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import frontmatter
import yaml

from core import validate, normalize

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Result:
    """Outcome of a single :func:`apply` call."""

    ok: bool
    """True if the file was successfully updated."""

    attempts: int
    """Total number of parse/validate attempts made (1 + retries consumed)."""

    errors: list[str] = field(default_factory=list)
    """Validation / parse error messages accumulated across attempts."""


@dataclass
class BatchReport:
    """Outcome of an :func:`apply_batch` call."""

    succeeded: int
    """Number of files successfully written."""

    failed: list[Path] = field(default_factory=list)
    """Paths that could not be written (malformed output or exception)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_yaml(raw: str) -> dict[str, Any] | None:
    """Extract and parse a YAML mapping from *raw*.

    Strategy:
    1. Look for a fenced ````yaml ... ```` block (case-insensitive,
       also matches ````yml````).
    2. If no fence is found, attempt to parse the whole string.

    Returns the parsed ``dict`` on success, or ``None`` if parsing fails or
    the result is not a mapping.
    """
    match = _FENCE_RE.search(raw)
    if match:
        yaml_text = match.group(1)
    else:
        yaml_text = raw

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return None

    if not isinstance(parsed, dict):
        return None

    return parsed


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically.

    Writes to a sibling temp file in the same directory, then calls
    ``os.replace`` to atomically swap it in.  If anything goes wrong, the
    temp file is removed in a ``finally`` block and the original *path* is
    left untouched.

    Note: ``os.replace`` is referenced as a module attribute (``os.replace``)
    so tests can monkeypatch ``wb.os.replace`` to simulate failures.
    """
    tmp_path: Path | None = None
    try:
        # NamedTemporaryFile in the same directory ensures os.replace is atomic
        # (same filesystem).  delete=False so we control cleanup.
        fd, tmp_str = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        tmp_path = Path(tmp_str)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)  # monkeypatched in tests via wb.os.replace
        tmp_path = None  # successfully replaced — no cleanup needed
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------


def apply(
    path: Path,
    raw: str,
    *,
    vocabulary: set[str],
    aliases: dict[str, str],
    retries: int = 2,
    regenerate: Callable[[], str] | None = None,
) -> Result:
    """Parse *raw* model output, validate it, and merge it into *path*'s frontmatter.

    The function attempts to parse and validate up to ``1 + retries`` times.
    On each failed attempt (parse error or validation failure) it calls
    *regenerate* (if supplied) to obtain a fresh candidate, then retries.
    If all attempts are exhausted, it returns a failed :class:`Result` and
    leaves *path* completely untouched.

    On success the merged frontmatter is written atomically:

    1. ``normalize.normalize_entities_dict`` is applied to ``entities``.
    2. ``validate.sanitize`` removes any injected / unknown keys.
    3. The sanitized keys are merged into the existing frontmatter (existing
       unknown keys are preserved).
    4. The result is written via :func:`_atomic_write`.

    Args:
        path:       Markdown file to update.
        raw:        Raw model response string, expected to contain a fenced
                    YAML block.
        vocabulary: Set of allowed topic strings for :func:`validate.check`.
        aliases:    Entity alias table for :func:`normalize.normalize_entities_dict`.
        retries:    Maximum number of additional attempts after the first.
                    Total attempts = ``1 + retries``.
        regenerate: Optional callable that returns a new *raw* string for the
                    next attempt.  Called at most *retries* times.

    Returns:
        :class:`Result` with ``ok=True`` and the file updated, or ``ok=False``
        and the file untouched.

    Raises:
        OSError: (or any exception from the underlying write) if parsing
                 succeeded but the atomic write itself failed.  In this case
                 no temp file is left behind.
    """
    max_attempts = 1 + retries
    all_errors: list[str] = []

    for attempt in range(1, max_attempts + 1):
        d = _extract_yaml(raw)

        if d is not None:
            ok, errors = validate.check(d, vocabulary)
        else:
            ok = False
            errors = ["could not extract a YAML mapping from model output"]

        if ok:
            # ---- Write path ----
            # 1. Normalize entities sub-dict.
            if isinstance(d.get("entities"), dict):
                d["entities"] = normalize.normalize_entities_dict(d["entities"], aliases)

            # 2. Sanitize (security boundary — drops injected keys).
            clean = validate.sanitize(d)

            # 3. Load existing frontmatter and merge.
            post = frontmatter.load(str(path))
            for k, v in clean.items():
                post[k] = v

            # 4. Atomic write (may raise — caller sees the exception; no leak).
            _atomic_write(path, frontmatter.dumps(post))

            return Result(ok=True, attempts=attempt, errors=[])

        # Failed this attempt — accumulate errors.
        all_errors.extend(errors)

        if attempt < max_attempts and regenerate is not None:
            raw = regenerate()
        elif attempt == max_attempts:
            break

    return Result(ok=False, attempts=max_attempts, errors=all_errors)


# ---------------------------------------------------------------------------
# apply_batch()
# ---------------------------------------------------------------------------

_FAILED_QUEUE = Path("failed-enrichments.jsonl")


def apply_batch(
    mapping: dict[Path, str],
    *,
    vocabulary: set[str],
    aliases: dict[str, str],
    retries: int = 0,
) -> BatchReport:
    """Apply :func:`apply` to each ``{Path: raw}`` pair in *mapping*.

    One bad item (failed validation OR unexpected exception) never aborts the
    batch.  All failures are collected and written to ``failed-enrichments.jsonl``
    in the current working directory so the caller can build a re-run queue.

    Args:
        mapping:    Mapping of ``{path: raw_model_response}``.
        vocabulary: Forwarded to :func:`apply`.
        aliases:    Forwarded to :func:`apply`.
        retries:    Forwarded to :func:`apply`.  Defaults to ``0`` (no retry)
                    for batch mode to keep throughput high.

    Returns:
        :class:`BatchReport` with counts and the list of failed paths.
    """
    succeeded = 0
    failed: list[Path] = []

    for path, raw in mapping.items():
        try:
            result = apply(path, raw, vocabulary=vocabulary, aliases=aliases, retries=retries)
            if result.ok:
                succeeded += 1
            else:
                logger.warning("apply failed for %s: %s", path, result.errors)
                failed.append(path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected error writing %s: %s", path, exc)
            failed.append(path)

    # Write re-run queue.
    if failed:
        try:
            with _FAILED_QUEUE.open("a", encoding="utf-8") as fh:
                for p in failed:
                    fh.write(json.dumps({"path": str(p)}) + "\n")
        except OSError as exc:
            logger.warning("could not write failed-enrichments queue: %s", exc)

    return BatchReport(succeeded=succeeded, failed=failed)


# ---------------------------------------------------------------------------
# Minimal CLI shim
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m core.enrichment_writeback <file.md>", file=sys.stderr)
        sys.exit(1)

    target = Path(sys.argv[1])
    raw_input = sys.stdin.read()

    # Best-effort: load vocab/aliases from config if available.
    _vocab: set[str] = set()
    _aliases: dict[str, str] = {}
    try:
        from core.config import load as _load_config  # type: ignore[import]

        cfg = _load_config()
        _vocab = set(cfg.get("vocabulary", []))
        _aliases = cfg.get("aliases", {})
    except Exception:  # noqa: BLE001
        pass

    res = apply(target, raw_input, vocabulary=_vocab, aliases=_aliases)
    if res.ok:
        print(f"ok: {target} updated in {res.attempts} attempt(s)")
    else:
        print(f"failed after {res.attempts} attempt(s): {res.errors}", file=sys.stderr)
        sys.exit(1)
