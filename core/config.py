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


def resolve_config_path(explicit: str | None) -> Path:
    """Resolve the path to signal-loom.yaml.

    Resolution order (first existing wins):
    1. *explicit* — if provided, return it as-is (user override).
    2. ``$SIGNAL_LOOM_CONFIG`` env var (full path to signal-loom.yaml).
    3. ``Path.cwd() / "config/signal-loom.yaml"`` — repo / headless install.
    4. ``PACKAGE_CONFIG_DIR / "signal-loom.yaml"`` — plugin install beside code.

    If none of 2-4 exist, returns the PACKAGE_CONFIG_DIR path so that
    ``ensure_configs`` can create it from the example there.
    """
    if explicit is not None:
        return Path(explicit)

    env_path = os.environ.get("SIGNAL_LOOM_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    cwd_path = Path.cwd() / "config" / "signal-loom.yaml"
    if cwd_path.exists():
        return cwd_path

    pkg_path = PACKAGE_CONFIG_DIR / "signal-loom.yaml"
    if pkg_path.exists():
        return pkg_path

    # None found — return the package dir path so ensure_configs can bootstrap there.
    return PACKAGE_CONFIG_DIR / "signal-loom.yaml"


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

    config_path = resolve_config_path(args.config)
    ensure_configs(config_path.parent)

    if not config_path.exists():
        print(
            f"config not found at {config_path}; "
            f"copy config/signal-loom.example.yaml → config/signal-loom.yaml",
            file=sys.stderr,
        )
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
