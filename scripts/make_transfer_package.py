#!/usr/bin/env python3
"""Build a portable Codex + VS Code transfer package.

The package is a local Codex marketplace root:

    signal-loom-codex-vscode-<stamp>/
      .agents/plugins/marketplace.json
      plugins/signal-loom/

Open ``plugins/signal-loom`` in VS Code. Add the package root as a Codex
marketplace, then install ``signal-loom@signal-loom-transfer``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAME = "signal-loom"
PACKAGE_SLUG = "signal-loom-codex-vscode"
DEFAULT_MARKETPLACE_NAME = "signal-loom-transfer"

COPY_ROOTS = [
    ".claude-plugin",
    ".codex-plugin",
    ".github",
    ".vscode",
    "agents",
    "codex",
    "config",
    "core",
    "docs",
    "hooks",
    "scripts",
    "skills",
    "tests",
    "LICENSE",
    "pyproject.toml",
    "README.md",
    "uv.lock",
]

SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
}

SKIP_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
}

SKIP_FILE_NAMES = {
    ".DS_Store",
    ".env",
    "failed-enrichments.jsonl",
    "index.json",
}


@dataclass(frozen=True)
class PackageResult:
    archive_path: Path
    staging_root: Path
    plugin_root: Path
    marketplace_path: Path
    marketplace_name: str
    codex_plugin_version: str


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def should_skip(path: Path, relative_to_source: Path) -> bool:
    parts = relative_to_source.parts
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    if path.name in SKIP_FILE_NAMES:
        return True
    # Skip every `.env` flavor (.env, .env.local, .env.production, …), not just
    # the exact ".env" name — they all carry secrets.
    if path.name == ".env" or path.name.startswith(".env."):
        return True
    if path.suffix in SKIP_FILE_SUFFIXES:
        return True
    if parts and parts[0] == "config":
        # Transfer example configs only, at ANY nesting depth. Real config files
        # — including nested per-purpose ones at config/<name>/signal-loom.yaml —
        # can contain personal sources, paths, or work-only vocabulary.
        return path.suffix in (".yaml", ".yml") and not path.name.endswith(".example.yaml")
    return False


def copy_root(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return

    for dirpath, dirnames, filenames in os.walk(source):
        current = Path(dirpath)
        rel_current = current.relative_to(source)
        dirnames[:] = [
            name
            for name in dirnames
            if not should_skip(current / name, Path(source.name) / rel_current / name)
        ]
        for filename in filenames:
            src = current / filename
            rel = Path(source.name) / rel_current / filename
            if should_skip(src, rel):
                continue
            dst = destination / rel_current / filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def copy_plugin_source(plugin_root: Path) -> None:
    plugin_root.mkdir(parents=True, exist_ok=True)
    for root_name in COPY_ROOTS:
        source = REPO_ROOT / root_name
        if not source.exists():
            continue
        destination = plugin_root / root_name
        copy_root(source, destination)


def stamp_codex_plugin_version(plugin_root: Path, stamp: str) -> str:
    manifest_path = plugin_root / ".codex-plugin/plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_version = str(manifest["version"]).split("+", 1)[0]
    version = f"{base_version}+codex.transfer-{stamp}"
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return version


def write_marketplace(package_root: Path, marketplace_name: str) -> Path:
    marketplace_path = package_root / ".agents/plugins/marketplace.json"
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": marketplace_name,
        "interface": {
            "displayName": "Signal Loom Transfer",
        },
        "owner": {
            "name": "local",
        },
        "description": "Portable local marketplace for signal-loom.",
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {
                    "source": "local",
                    "path": f"./plugins/{PLUGIN_NAME}",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_USE",
                },
                "category": "Productivity",
                "description": "Signal Loom for Codex and VS Code.",
            }
        ],
    }
    marketplace_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return marketplace_path


def write_package_readme(
    package_root: Path,
    *,
    marketplace_name: str,
    codex_plugin_version: str,
) -> None:
    readme = f"""# signal-loom Codex + VS Code transfer package

This folder is a portable Codex local marketplace and VS Code workspace source.

## Layout

- `.agents/plugins/marketplace.json` points Codex at the local plugin source.
- `plugins/signal-loom/` is the project folder to open in VS Code.
- The staged Codex plugin version is `{codex_plugin_version}` so Codex refreshes
  its cache when you install this transfer bundle.

## Setup On Another Machine

1. Install prerequisites: Codex CLI/app, VS Code, Python 3.12+, and `uv`.
2. Open the source folder:

   ```bash
   cd plugins/signal-loom
   code .
   uv sync --extra dev
   uv run pytest -q
   ```

3. Add and install the local Codex marketplace from the package root:

   ```bash
   PACKAGE_ROOT="$(cd ../.. && pwd)"
   codex plugin marketplace add "$PACKAGE_ROOT"
   codex plugin add signal-loom@{marketplace_name}
   ```

4. Start a new Codex thread with plugins enabled and try:

   ```text
   $pipeline refresh my sources
   ```

The package intentionally excludes `.venv`, `.git`, caches, `.env`, generated
`content/`, `index.json`, `failed-enrichments.jsonl`, and non-example
`config/*.yaml` files. There is no auto-bootstrap — scaffold a project config
with `python -m core.init --to .` before the first run.
"""
    (package_root / "README-transfer.md").write_text(readme, encoding="utf-8")


def create_archive(package_root: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / f"{package_root.name}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(package_root, arcname=package_root.name)
    return archive_path


def build_package(
    out_dir: Path,
    *,
    stamp: str | None = None,
    marketplace_name: str = DEFAULT_MARKETPLACE_NAME,
    keep_staging: bool = False,
) -> PackageResult:
    stamp = stamp or utc_stamp()
    parent = out_dir if keep_staging else Path(tempfile.mkdtemp(prefix="signal-loom-transfer-"))
    staging_root = parent / f"{PACKAGE_SLUG}-{stamp}"
    plugin_root = staging_root / "plugins" / PLUGIN_NAME

    if staging_root.exists():
        shutil.rmtree(staging_root)
    copy_plugin_source(plugin_root)
    version = stamp_codex_plugin_version(plugin_root, stamp)
    marketplace_path = write_marketplace(staging_root, marketplace_name)
    write_package_readme(
        staging_root,
        marketplace_name=marketplace_name,
        codex_plugin_version=version,
    )
    archive_path = create_archive(staging_root, out_dir)
    if not keep_staging:
        shutil.rmtree(parent, ignore_errors=True)

    return PackageResult(
        archive_path=archive_path,
        staging_root=staging_root,
        plugin_root=plugin_root,
        marketplace_path=marketplace_path,
        marketplace_name=marketplace_name,
        codex_plugin_version=version,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "dist",
        help="directory for the .tar.gz package",
    )
    parser.add_argument(
        "--marketplace-name",
        default=DEFAULT_MARKETPLACE_NAME,
        help="Codex marketplace name used for `codex plugin add`",
    )
    parser.add_argument("--stamp", help="override UTC stamp, useful for tests")
    parser.add_argument(
        "--keep-staging",
        action="store_true",
        help="leave the staged package directory beside the archive",
    )
    args = parser.parse_args(argv)

    result = build_package(
        args.out_dir.resolve(),
        stamp=args.stamp,
        marketplace_name=args.marketplace_name,
        keep_staging=args.keep_staging,
    )

    print(json.dumps(
        {
            "archive": str(result.archive_path),
            "marketplace_name": result.marketplace_name,
            "codex_plugin_version": result.codex_plugin_version,
            "install": [
                f"tar -xzf {result.archive_path.name}",
                f"codex plugin marketplace add /path/to/{result.archive_path.stem.removesuffix('.tar')}",
                f"codex plugin add {PLUGIN_NAME}@{result.marketplace_name}",
            ],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
