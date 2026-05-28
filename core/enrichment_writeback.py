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


def default_failed_queue_path(config_path: Path) -> Path:
    """Return the default failed-enrichment queue beside the active data root."""
    config_path = Path(config_path)
    if config_path.parent.name == "config":
        return config_path.parent.parent / _FAILED_QUEUE.name
    return config_path.parent / _FAILED_QUEUE.name


def append_failed_queue(
    path: Path,
    errors: list[str],
    *,
    queue_path: Path | None = None,
) -> None:
    """Append one failed enrichment entry to the re-run queue."""
    target_queue = Path(queue_path) if queue_path is not None else _FAILED_QUEUE
    target_queue.parent.mkdir(parents=True, exist_ok=True)
    with target_queue.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"path": str(path), "errors": errors}) + "\n")


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
            for p in failed:
                append_failed_queue(p, [], queue_path=_FAILED_QUEUE)
        except OSError as exc:
            logger.warning("could not write failed-enrichments queue: %s", exc)

    return BatchReport(succeeded=succeeded, failed=failed)


# ---------------------------------------------------------------------------
# CLI shim
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``python -m core.enrichment_writeback apply <file.md>``.

    Reads raw model output from stdin, loads settings (``--config`` overrides
    the default ``config/signal-loom.yaml``), then calls :func:`apply` and
    prints ``OK <path>`` on success or the validation errors on failure.

    Exits 0 on success, 1 on validation failure or bad arguments.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m core.enrichment_writeback",
        description="Apply enrichment YAML (from stdin) to a markdown file's frontmatter.",
    )
    sub = parser.add_subparsers(dest="command")

    apply_p = sub.add_parser("apply", help="Apply raw YAML from stdin (or --raw-file) to <file.md>")
    apply_p.add_argument("path", help="Markdown file to update")
    apply_p.add_argument(
        "--config",
        default=None,
        help="Path to signal-loom.yaml (default: auto-discovered)",
    )
    apply_p.add_argument(
        "--raw-file",
        default=None,
        metavar="PATH",
        help=(
            "Read raw model output from this file instead of stdin. "
            "Use this to avoid shell-interpolation of model output. "
            "Never interpolate model output into a shell command."
        ),
    )
    apply_p.add_argument(
        "--failed-queue",
        default=None,
        metavar="PATH",
        help=(
            "Append validation failures to this queue. Defaults to "
            "failed-enrichments.jsonl beside the active signal-loom root."
        ),
    )

    args = parser.parse_args(argv)

    if args.command != "apply":
        parser.print_help(sys.stderr)
        return 1

    target = Path(args.path)
    if args.raw_file is not None:
        raw_input = Path(args.raw_file).read_text(encoding="utf-8")
    else:
        raw_input = sys.stdin.read()

    # Load vocab and aliases via config — fail hard on config errors (#4).
    from core.config import (
        ConfigError,
        ensure_configs,
        load_aliases,
        load_settings,
        load_vocabulary,
        resolve_config_path,
    )

    config_path = resolve_config_path(args.config)
    ensure_configs(config_path.parent)
    failed_queue = (
        Path(args.failed_queue)
        if args.failed_queue is not None
        else default_failed_queue_path(config_path)
    )

    if not config_path.exists():
        print(
            f"config not found at {config_path}; "
            f"copy config/signal-loom.example.yaml → config/signal-loom.yaml",
            file=sys.stderr,
        )
        return 1

    try:
        settings = load_settings(config_path)
        _vocab = load_vocabulary(settings.topics_path)
        _aliases = load_aliases(settings.aliases_path)
    except Exception as exc:  # noqa: BLE001
        print(f"error loading config from {config_path}: {exc}", file=sys.stderr)
        return 1

    # Empty vocab fail-fast (#7)
    if not _vocab:
        print(
            "config/topics.yaml has no topics — add at least one",
            file=sys.stderr,
        )
        return 1

    res = apply(target, raw_input, vocabulary=_vocab, aliases=_aliases)
    if res.ok:
        print(f"OK {target}")
        return 0
    else:
        for err in res.errors:
            print(err, file=sys.stderr)
        try:
            append_failed_queue(target, res.errors, queue_path=failed_queue)
            print(f"queued failed enrichment: {failed_queue}", file=sys.stderr)
        except OSError as exc:
            print(f"could not write failed-enrichments queue: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
