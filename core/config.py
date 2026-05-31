"""core/config.py — Load and validate signal-loom configuration.

Handles four config artefacts:
  * sources.yaml         — ordered mapping of named source definitions
  * signal-loom.yaml     — global pipeline settings
  * topics.yaml          — controlled topic vocabulary for cross-source clustering
  * entity-aliases.yaml  — variant → canonical entity name mapping

v1 supports source types: rss, youtube, listing.
Types podcast / whisper / substack-full are v1.1 and are rejected with a
clear pointer so users know what to expect.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# v1 supported types and explicitly deferred v1.1 types
# ---------------------------------------------------------------------------

_V1_TYPES: frozenset[str] = frozenset({"rss", "youtube", "listing"})
_V11_TYPES: frozenset[str] = frozenset({"podcast", "whisper", "substack-full"})

# ---------------------------------------------------------------------------
# Package-level config directory (where *.example.yaml files live)
# ---------------------------------------------------------------------------

PACKAGE_CONFIG_DIR: Path = Path(__file__).resolve().parent.parent / "config"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigError(Exception):
    """Raised when a config file contains invalid or unsupported values."""


class ConfigNotFoundError(ConfigError):
    """Raised when no signal-loom config can be discovered.

    Carries the list of paths searched and a human-readable message that
    tells the caller exactly how to fix the situation (run init, pass
    ``--config``, or place ``signal-loom.yaml`` in the project).
    """

    def __init__(
        self,
        searched: list[Path],
        start: Path,
        *,
        reason: str | None = None,
        discovered: list[Path] | None = None,
    ):
        self.searched = list(searched)
        self.start = start
        self.reason = reason
        # Configs found *under* the project by a recursive scan, that the
        # walk-up did NOT auto-discover (e.g. a per-purpose config nested at
        # config/<name>/signal-loom.yaml). Surfacing these prevents users from
        # scaffolding a duplicate over an already-configured project.
        self.discovered = list(discovered or [])

        searched_str = "\n  ".join(str(p) for p in searched) if searched else "(none)"
        hint = (
            "To fix:\n"
            "  • Run `python -m core.init --to <dir>` to scaffold one, or\n"
            "  • Pass `--config <path>` to point at an existing file, or\n"
            "  • Place a `signal-loom.yaml` in this project (or a parent directory)."
        )
        existing_block = ""
        if self.discovered:
            listed = "\n  ".join(str(p) for p in self.discovered)
            existing_block = (
                "Found existing signal-loom config(s) under this project that the\n"
                "walk-up did NOT auto-discover (non-standard / nested location):\n  "
                + listed
                + "\n→ Re-run with `--config <one of the paths above>` to use it,\n"
                + "  rather than scaffolding a new config.\n\n"
            )
        message = (
            (f"{reason}\n\n" if reason else "")
            + "No signal-loom config found.\n"
            + f"Walked up from: {start}\n"
            + f"Searched:\n  {searched_str}\n\n"
            + existing_block
            + hint
        )
        super().__init__(message)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SourceConfig:
    """Configuration for a single content source."""

    name: str
    type: str
    feed_url: str
    output_dir: str
    tags: list[str] = field(default_factory=list)
    perspective: str | None = None
    scrape_limit: int = 10
    scrape_full_content: bool = False
    fetch_method: str = "auto"
    keyword_filter: dict | None = None
    listing_link_pattern: str | None = None
    enabled: bool = True


@dataclass
class Settings:
    """Global pipeline settings."""

    enrichment_model: str = "claude-sonnet-4-6"
    content_dir: str = "content"
    index_path: str = "index.json"
    topics_path: str = "topics.yaml"
    aliases_path: str = "entity-aliases.yaml"
    sources_path: str = "sources.yaml"


# ---------------------------------------------------------------------------
# Config discovery and auto-bootstrap
# ---------------------------------------------------------------------------


# Filenames searched by walk-up, in precedence order. The first one found at a
# given directory wins before moving to the parent.
_WALKUP_FILENAMES: tuple[str, ...] = (
    "signal-loom.yaml",
    ".signal-loom.yaml",
    ".signal-loom/config.yaml",
    "config/signal-loom.yaml",  # legacy layout (pre-v0.2)
)


def _walkup_start(cwd: Path | None, project_dir: Path | None) -> Path:
    """Choose where to start the walk-up.

    Precedence: explicit project_dir → $CLAUDE_PROJECT_DIR → explicit cwd → os.getcwd().
    The Claude Code env var is the right answer when present because the host
    has already resolved 'which project the agent is in'.
    """
    if project_dir is not None:
        return Path(project_dir).resolve()
    env_project = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_project:
        return Path(env_project).resolve()
    if cwd is not None:
        return Path(cwd).resolve()
    return Path.cwd().resolve()


def _walkup_boundary() -> Path:
    """Return the directory the walk-up must NOT escape (defaults to $HOME)."""
    home = os.environ.get("HOME")
    return Path(home).resolve() if home else Path("/")


# Directories never worth scanning when hunting for stray configs.
_SCAN_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "dist",
        "build",
        ".astro",
        ".next",
        ".cache",
    }
)


def find_existing_configs(start: Path, *, limit: int = 25) -> list[Path]:
    """Recursively find ``signal-loom.yaml`` files anywhere under *start*.

    This catches configs the walk-up resolver intentionally does NOT find —
    e.g. a per-purpose config nested at
    ``config/signal-loom-recipe-trends/signal-loom.yaml``. It exists so the
    "no config found" path can suggest ``--config <path>`` instead of letting
    a user (or agent) scaffold a duplicate over an already-configured project.

    Bounded by *limit* and skips heavy/irrelevant directories
    (``.git``, ``.venv``, ``node_modules``, …). Returns sorted unique paths.
    """
    try:
        if not start.is_dir():
            return []
    except OSError:
        return []

    found: list[Path] = []
    seen: set[Path] = set()
    for path in start.rglob("signal-loom.yaml"):
        if any(part in _SCAN_SKIP_DIRS for part in path.parts):
            continue
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(path)
        if len(found) >= limit:
            break
    return sorted(found)


def _walkup_search(start: Path) -> tuple[Path | None, list[Path]]:
    """Walk from *start* upward looking for any of _WALKUP_FILENAMES.

    Returns (found_path, all_candidate_paths_checked).
    Stops at the $HOME boundary or filesystem root, inclusive.
    """
    boundary = _walkup_boundary()
    searched: list[Path] = []
    current = start
    while True:
        for name in _WALKUP_FILENAMES:
            candidate = current / name
            searched.append(candidate)
            if candidate.is_file():
                return candidate, searched
        # Stop AFTER checking the boundary directory itself.
        if current == boundary or current.parent == current:
            return None, searched
        # Don't ascend above $HOME even if cwd started inside $HOME.
        try:
            if boundary in current.parents and current.parent not in (boundary, *boundary.parents):
                pass  # parent is still below or at boundary — keep walking
        except Exception:
            pass
        if current == boundary:
            return None, searched
        current = current.parent


def resolve_config_path(
    explicit: str | Path | None = None,
    *,
    cwd: Path | None = None,
    project_dir: Path | None = None,
) -> Path:
    """Resolve the path to signal-loom.yaml.

    Precedence (first existing file wins; otherwise raises ConfigNotFoundError):

      1. *explicit* — the CLI ``--config`` flag. Must exist or raises.
      2. ``$CLAUDE_PLUGIN_OPTION_CONFIG_PATH`` — Claude Code ``userConfig`` output.
         Set once at plugin-enable time; missing-file falls through.
      3. ``$SIGNAL_LOOM_CONFIG`` — legacy env var. **Deprecated**; emits a
         DeprecationWarning. Missing-file falls through.
      4. Walk up from ``project_dir`` (or ``$CLAUDE_PROJECT_DIR``, or ``cwd``, or
         ``os.getcwd()``) checking, in each directory:
           - ``signal-loom.yaml``
           - ``.signal-loom.yaml``
           - ``.signal-loom/config.yaml``
           - ``config/signal-loom.yaml``  (legacy layout)
         Stops at ``$HOME`` or filesystem root.

    Raises:
        ConfigNotFoundError: nothing was discovered.
    """
    # (1) explicit
    if explicit is not None:
        p = Path(explicit)
        if p.is_file():
            return p
        raise ConfigNotFoundError(
            [p],
            start=p.parent,
            reason=f"--config path does not exist: {p}",
        )

    # (2) Claude Code userConfig
    user_cfg = os.environ.get("CLAUDE_PLUGIN_OPTION_CONFIG_PATH")
    if user_cfg:
        p = Path(user_cfg)
        if p.is_file():
            return p
        # Fall through silently — the user may have an unset userConfig with
        # a default placeholder; we should still discover a local config.

    # (3) Legacy $SIGNAL_LOOM_CONFIG (deprecated)
    legacy = os.environ.get("SIGNAL_LOOM_CONFIG")
    if legacy:
        p = Path(legacy)
        if p.is_file():
            import warnings

            warnings.warn(
                "$SIGNAL_LOOM_CONFIG is deprecated; use Claude Code userConfig "
                "(CLAUDE_PLUGIN_OPTION_CONFIG_PATH) or place a signal-loom.yaml "
                "in your project root for walk-up discovery.",
                DeprecationWarning,
                stacklevel=2,
            )
            return p

    # (4) Walk-up discovery
    start = _walkup_start(cwd, project_dir)
    found, searched = _walkup_search(start)
    if found is not None:
        return found

    # Nothing on the walk-up path. Before giving up, scan the project for
    # configs in non-standard / nested locations so the error can point at
    # them (use --config) instead of inviting a duplicate scaffold.
    discovered = find_existing_configs(start)
    raise ConfigNotFoundError(searched, start=start, discovered=discovered)


def ensure_configs(config_dir: Path) -> list[str]:
    """Copy missing *.yaml configs from *.example.yaml in *config_dir*.

    For each base in ["signal-loom", "sources", "topics", "entity-aliases"]:
    - If ``<base>.yaml`` is missing but ``<base>.example.yaml`` exists, copy
      example → yaml and log the action.

    Returns a list of created file names (basenames only).
    """
    bases = ["signal-loom", "sources", "topics", "entity-aliases"]
    created: list[str] = []

    for base in bases:
        target = config_dir / f"{base}.yaml"
        example = config_dir / f"{base}.example.yaml"
        if not target.exists() and example.exists():
            shutil.copy2(example, target)
            created.append(f"{base}.yaml")
            logger.info(
                "created %s from example — edit it to add your sources/topics",
                target,
            )

    return created


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_sources(path: str) -> list[SourceConfig]:
    """Parse *path* (a YAML mapping of key → source-dict) into SourceConfig objects.

    Only enabled sources are returned.  Raises ConfigError for:
    * v1.1 types (podcast, whisper, substack-full) — deferred, not supported yet
    * any other unrecognised type
    * invalid keyword_filter.mode (must be "any" or "all")
    """
    with open(path) as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    sources: list[SourceConfig] = []
    for key, data in raw.items():
        if not isinstance(data, dict):
            raise ConfigError(f"source '{key}': expected a mapping, got {type(data).__name__}")

        src_type: str = data.get("type", "")

        if src_type in _V11_TYPES:
            raise ConfigError(
                f"source '{key}': type '{src_type}' is a v1.1 feature — not yet supported in v1"
            )

        if src_type not in _V1_TYPES:
            raise ConfigError(
                f"source '{key}': unknown type '{src_type}' — "
                f"v1 supports {sorted(_V1_TYPES)}"
            )

        output_dir_raw: str = data.get("output_dir", "")
        # Containment check: reject absolute paths and path-traversal segments.
        # This guard applies only to sources loaded from YAML; programmatic
        # SourceConfig(...) construction is unaffected.
        if os.path.isabs(output_dir_raw):
            raise ConfigError(
                f"source '{key}': output_dir '{output_dir_raw}' must be a relative path, "
                "not an absolute path."
            )
        # Normalise to detect '..' after joining (e.g. "a/../../../etc")
        _normalised = os.path.normpath(output_dir_raw) if output_dir_raw else ""
        if any(part == ".." for part in _normalised.replace("\\", "/").split("/")):
            raise ConfigError(
                f"source '{key}': output_dir '{output_dir_raw}' must not contain '..' segments."
            )

        # Validate keyword_filter.mode if present
        kf = data.get("keyword_filter") or None
        if kf is not None:
            mode = kf.get("mode", "any")
            if mode not in ("any", "all"):
                raise ConfigError(
                    f"source '{key}': keyword_filter.mode '{mode}' is invalid — "
                    "must be 'any' or 'all'"
                )

        src = SourceConfig(
            name=data.get("name", key),
            type=src_type,
            feed_url=data.get("feed_url", ""),
            output_dir=output_dir_raw,
            tags=list(data.get("tags") or []),
            perspective=data.get("perspective"),
            scrape_limit=int(data.get("scrape_limit", 10)),
            scrape_full_content=bool(data.get("scrape_full_content", False)),
            fetch_method=str(data.get("fetch_method", "auto")),
            keyword_filter=kf,
            listing_link_pattern=data.get("listing_link_pattern") or None,
            enabled=bool(data.get("enabled", True)),
        )

        if src.enabled:
            sources.append(src)

    return sources


def load_settings(path: str | Path) -> Settings:
    """Parse *path* into a Settings object, applying defaults for absent keys.

    All relative paths (content_dir, index_path, topics_path, aliases_path,
    sources_path) are resolved relative to the config file's parent directory
    so that a plugin install keeps its content/index beside the config file,
    regardless of the user's cwd.
    """
    path = Path(path)
    config_dir = path.parent

    with open(path) as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    def _resolve(val: str | None, default: str) -> str:
        """Return *val* (or *default*) resolved relative to config_dir if relative."""
        v = val if val is not None else default
        p = Path(v)
        if not p.is_absolute():
            return str((config_dir / p).resolve())
        return v

    if config_dir.name == "config":
        default_content_dir = "../content"
        default_index_path = "../index.json"
    else:
        default_content_dir = "content"
        default_index_path = "index.json"

    return Settings(
        enrichment_model=raw.get("enrichment_model", "claude-sonnet-4-6"),
        content_dir=_resolve(raw.get("content_dir"), default_content_dir),
        index_path=_resolve(raw.get("index_path"), default_index_path),
        topics_path=_resolve(raw.get("topics_path"), "topics.yaml"),
        aliases_path=_resolve(raw.get("aliases_path"), "entity-aliases.yaml"),
        sources_path=_resolve(raw.get("sources_path"), "sources.yaml"),
    )


def resolve_source_output_dirs(
    sources: list[SourceConfig],
    settings: Settings,
) -> list[SourceConfig]:
    """Return copies of *sources* with output_dir resolved under content_dir.

    Source YAML intentionally keeps ``output_dir`` portable and relative.  The
    pipeline materializes those paths before scraping so plugin invocations do
    not accidentally write into the user's current working directory.

    Back-compat rule: examples historically used ``content/<source>`` while
    settings use ``content_dir``.  If a source path already starts with the
    content directory's basename, resolve it against ``content_dir.parent``.
    Otherwise resolve it as a child of ``content_dir``.
    """
    content_dir = Path(settings.content_dir)
    resolved: list[SourceConfig] = []

    for src in sources:
        output_dir = Path(src.output_dir)
        if output_dir.is_absolute():
            materialized = output_dir
        elif output_dir.parts and output_dir.parts[0] == content_dir.name:
            materialized = content_dir.parent / output_dir
        else:
            materialized = content_dir / output_dir
        resolved.append(replace(src, output_dir=str(materialized)))

    return resolved


def load_vocabulary(path: str) -> set[str]:
    """Parse *path* into a set of topic strings.

    Accepts either:
    * a plain YAML list  →  each element becomes a topic
    * a mapping with a ``topics:`` key whose value is a list
    """
    with open(path) as fh:
        raw = yaml.safe_load(fh)

    if isinstance(raw, list):
        return {str(item) for item in raw}

    if isinstance(raw, dict):
        items = raw.get("topics", [])
        if isinstance(items, list):
            return {str(item) for item in items}

    raise ConfigError(f"topics file '{path}' must be a YAML list or a mapping with a 'topics' key")


def load_aliases(path: str) -> dict[str, str]:
    """Parse *path* into a variant → canonical mapping.

    An empty or null file returns an empty dict.
    """
    with open(path) as fh:
        raw = yaml.safe_load(fh)

    if raw is None:
        return {}

    if not isinstance(raw, dict):
        raise ConfigError(f"aliases file '{path}' must be a YAML mapping")

    return {str(k): str(v) for k, v in raw.items()}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point: ``python -m core.config --print <field>``.

    Loads settings from ``--config`` (default: auto-discovered via
    resolve_config_path) and prints the value of the named Settings field
    to stdout.

    Exits 0 on success, 1 on error.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m core.config",
        description="Print a Settings field value from signal-loom.yaml.",
    )
    parser.add_argument("--print", dest="field", required=True, help="Settings field to print")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to signal-loom.yaml (default: auto-discovered)",
    )
    args = parser.parse_args(argv)

    try:
        config_path = resolve_config_path(args.config)
    except ConfigNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        settings = load_settings(config_path)
    except Exception as exc:  # noqa: BLE001
        print(f"error loading config: {exc}", file=sys.stderr)
        return 1

    if not hasattr(settings, args.field):
        print(f"error: unknown field '{args.field}'", file=sys.stderr)
        return 1

    print(getattr(settings, args.field))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
