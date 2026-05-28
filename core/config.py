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

from dataclasses import dataclass, field
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# v1 supported types and explicitly deferred v1.1 types
# ---------------------------------------------------------------------------

_V1_TYPES: frozenset[str] = frozenset({"rss", "youtube", "listing"})
_V11_TYPES: frozenset[str] = frozenset({"podcast", "whisper", "substack-full"})


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
    topics_path: str = "config/topics.yaml"
    aliases_path: str = "config/entity-aliases.yaml"
    sources_path: str = "config/sources.yaml"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_sources(path: str) -> list[SourceConfig]:
    """Parse *path* (a YAML mapping of key → source-dict) into SourceConfig objects.

    Only enabled sources are returned.  Raises ConfigError for:
    * v1.1 types (podcast, whisper, substack-full) — deferred, not supported yet
    * any other unrecognised type
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

        src = SourceConfig(
            name=data.get("name", key),
            type=src_type,
            feed_url=data.get("feed_url", ""),
            output_dir=data.get("output_dir", ""),
            tags=list(data.get("tags") or []),
            perspective=data.get("perspective"),
            scrape_limit=int(data.get("scrape_limit", 10)),
            scrape_full_content=bool(data.get("scrape_full_content", False)),
            fetch_method=str(data.get("fetch_method", "auto")),
            keyword_filter=data.get("keyword_filter") or None,
            listing_link_pattern=data.get("listing_link_pattern") or None,
            enabled=bool(data.get("enabled", True)),
        )

        if src.enabled:
            sources.append(src)

    return sources


def load_settings(path: str) -> Settings:
    """Parse *path* into a Settings object, applying defaults for absent keys."""
    with open(path) as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    return Settings(
        enrichment_model=raw.get("enrichment_model", "claude-sonnet-4-6"),
        content_dir=raw.get("content_dir", "content"),
        index_path=raw.get("index_path", "index.json"),
        topics_path=raw.get("topics_path", "config/topics.yaml"),
        aliases_path=raw.get("aliases_path", "config/entity-aliases.yaml"),
        sources_path=raw.get("sources_path", "config/sources.yaml"),
    )


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
