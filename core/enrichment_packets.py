"""Generate model-work packets for Codex-native enrichment.

This module is deliberately model-provider agnostic. It reads unenriched
markdown files, builds the same canonical enrichment prompt used by the
Anthropic API path, and emits JSONL packets for an interactive agent runtime to
complete. The raw model output still flows through core.enrichment_writeback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable

import frontmatter

from core import prompts
from core.config import (
    ConfigError,
    ensure_configs,
    load_settings,
    load_vocabulary,
    resolve_config_path,
)


def needs_enrichment(path: Path) -> bool:
    """Return True when a markdown file lacks enriched: true frontmatter."""
    try:
        post = frontmatter.load(str(path))
    except Exception:
        return True
    return post.metadata.get("enriched") is not True


def iter_unenriched(content_dir: Path, *, limit: int = 0) -> Iterable[Path]:
    """Yield unenriched markdown paths under content_dir in stable order."""
    count = 0
    for path in sorted(content_dir.rglob("*.md")):
        if not needs_enrichment(path):
            continue
        yield path
        count += 1
        if limit > 0 and count >= limit:
            break


def build_packet(path: Path, vocabulary: set[str], *, max_chars: int = 50000) -> dict:
    """Build one JSON-serializable enrichment work packet."""
    post = frontmatter.load(str(path))
    prompt = prompts.build(post.content, vocabulary, max_chars=max_chars)
    return {
        "path": str(path.resolve()),
        "title": str(post.metadata.get("title") or path.stem),
        "article_chars": len(post.content),
        "prompt_chars": len(prompt),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "output_contract": "Return only one fenced ```yaml block. No preamble.",
        "prompt": prompt,
    }


def build_packets(
    *,
    config: str | None = None,
    limit: int = 0,
    max_chars: int = 50000,
) -> list[dict]:
    """Load config and return packets for unenriched files."""
    config_path = resolve_config_path(config)
    ensure_configs(config_path.parent)
    if not config_path.exists():
        raise ConfigError(
            f"config not found at {config_path}; copy config/signal-loom.example.yaml"
        )

    settings = load_settings(config_path)
    vocabulary = load_vocabulary(settings.topics_path)
    if not vocabulary:
        raise ConfigError("config/topics.yaml has no topics; add at least one")

    content_dir = Path(settings.content_dir)
    paths = list(iter_unenriched(content_dir, limit=limit))
    return [build_packet(path, vocabulary, max_chars=max_chars) for path in paths]


def _write_jsonl(packets: list[dict], out: str | None) -> None:
    lines = [json.dumps(packet, ensure_ascii=False) for packet in packets]
    payload = "\n".join(lines)
    if payload:
        payload += "\n"

    if out:
        Path(out).write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for Codex-native enrichment packet generation."""
    parser = argparse.ArgumentParser(
        prog="python -m core.enrichment_packets",
        description="Emit JSONL enrichment packets for Codex-native model work.",
    )
    sub = parser.add_subparsers(dest="command")

    emit_p = sub.add_parser("emit", help="Emit unenriched article packets as JSONL")
    emit_p.add_argument("--config", default=None, help="Path to signal-loom.yaml")
    emit_p.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Maximum packets to emit (0 = all unenriched files)",
    )
    emit_p.add_argument(
        "--max-chars",
        type=int,
        default=50000,
        help="Maximum article body characters per packet prompt",
    )
    emit_p.add_argument("--out", default=None, help="Write JSONL to this path")

    args = parser.parse_args(argv)
    if args.command != "emit":
        parser.print_help(sys.stderr)
        return 1

    try:
        packets = build_packets(
            config=args.config,
            limit=args.max_files,
            max_chars=args.max_chars,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _write_jsonl(packets, args.out)
    print(f"emitted {len(packets)} enrichment packet(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
