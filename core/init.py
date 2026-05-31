"""core/init.py — `python -m core.init`: scaffold a signal-loom config in a project.

Explicit, opt-in replacement for the old silent ensure_configs-on-run pattern.
Copies a template set of config files (signal-loom.yaml, sources.yaml,
topics.yaml, entity-aliases.yaml) into the user's project directory and refuses
to overwrite existing files unless ``--force`` is given.

Why this exists
---------------
The runtime resolver (``core.config.resolve_config_path``) no longer falls back
to the plugin's bundled example files. Missing config is now a loud, actionable
error pointing the user here. This module is what the error tells them to run.

Usage
-----
    python -m core.init                              # writes into cwd
    python -m core.init --to ./my-project            # writes into a directory
    python -m core.init --template minimal           # picks a template set
    python -m core.init --force                      # overwrite existing files

Adding new templates
--------------------
Drop a directory under ``examples/<name>/`` containing the four canonical
filenames. ``--template <name>`` will pick it up. The default "minimal" template
is sourced from ``config/*.example.yaml`` for back-compat with the existing
example files.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Optional

from core.config import PACKAGE_CONFIG_DIR, find_existing_configs

# Project root = parent of the `core/` package.
_PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent
_EXAMPLES_DIR: Path = _PACKAGE_ROOT / "examples"

_TARGET_FILENAMES: tuple[str, ...] = (
    "signal-loom.yaml",
    "sources.yaml",
    "topics.yaml",
    "entity-aliases.yaml",
)


def _resolve_template_dir(template: str) -> Path | None:
    """Return the directory containing template files for *template*, or None.

    Resolution:
      1. ``examples/<template>/``  — new layout, one subdir per template
      2. For the special name ``minimal``: ``config/`` (uses ``*.example.yaml``)
    """
    candidate = _EXAMPLES_DIR / template
    if candidate.is_dir():
        return candidate
    if template == "minimal" and PACKAGE_CONFIG_DIR.is_dir():
        # Verify at least one expected example file exists before claiming it.
        if any((PACKAGE_CONFIG_DIR / f"{stem}.example.yaml").is_file() for stem in
               ("signal-loom", "sources", "topics", "entity-aliases")):
            return PACKAGE_CONFIG_DIR
    return None


def _source_path_for(template_dir: Path, target_name: str) -> Path | None:
    """Find the source file in *template_dir* for *target_name*.

    Two template layouts are supported, and the canonical template ALWAYS wins:
      - ``<stem>.example.<ext>``  — the legacy ``config/`` layout. This is the
        committed source of truth. It is checked FIRST so that a stray, non-
        example ``<target_name>`` left in the package config dir (e.g. an
        auto-bootstrap leftover like ``config/signal-loom.yaml``) can NOT
        shadow the real template. That shadowing was a real footgun: such a
        leftover could carry a ``content_dir: ../content`` that escapes the
        project when scaffolded to a root.
      - ``<target_name>``         — the ``examples/<template>/`` layout, which
        uses bare filenames and has no ``.example`` variant. Used as fallback.

    Returns the first existing match, or None.
    """
    p = Path(target_name)
    example = template_dir / f"{p.stem}.example{p.suffix}"
    if example.is_file():
        return example

    direct = template_dir / target_name
    if direct.is_file():
        return direct

    return None


def _write(template_dir: Path, target_dir: Path, force: bool) -> tuple[list[str], list[str]]:
    """Copy template files into target_dir.

    Returns (created, skipped) — basenames only. If any target already exists
    and force is False, ``created`` will be empty and the existing file's name
    appears in ``skipped``.
    """
    # Refusal pass: check ALL targets up-front so we don't half-write a config.
    existing_targets: list[str] = []
    plan: list[tuple[Path, Path]] = []
    for name in _TARGET_FILENAMES:
        src = _source_path_for(template_dir, name)
        if src is None:
            continue
        dst = target_dir / name
        if dst.exists() and not force:
            existing_targets.append(name)
        plan.append((src, dst))

    if existing_targets and not force:
        return [], existing_targets

    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    for src, dst in plan:
        shutil.copy2(src, dst)
        created.append(dst.name)
    return created, []


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m core.init",
        description=(
            "Scaffold a signal-loom config (signal-loom.yaml + sources.yaml + "
            "topics.yaml + entity-aliases.yaml) in a project directory."
        ),
    )
    parser.add_argument(
        "--to",
        default=".",
        metavar="DIR",
        help="Target directory to write config files into (default: current directory).",
    )
    parser.add_argument(
        "--template",
        default="minimal",
        metavar="NAME",
        help='Template name to copy from (default: "minimal").',
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files (otherwise the command refuses).",
    )

    args = parser.parse_args(argv)

    template_dir = _resolve_template_dir(args.template)
    if template_dir is None:
        available: list[str] = []
        if _EXAMPLES_DIR.is_dir():
            available.extend(sorted(d.name for d in _EXAMPLES_DIR.iterdir() if d.is_dir()))
        if "minimal" not in available:
            available.insert(0, "minimal")
        print(
            f"unknown template: {args.template!r}\n"
            f"available templates: {', '.join(available) or '(none)'}",
            file=sys.stderr,
        )
        return 2

    target_dir = Path(args.to).resolve()

    # Guard against scaffolding a duplicate over a project that is ALREADY
    # configured — including configs in nested / non-standard locations the
    # walk-up resolver won't auto-discover (e.g. config/<name>/signal-loom.yaml).
    # The exact target file is handled by the normal overwrite-refusal below;
    # here we only care about *other* existing configs under the target.
    if not args.force:
        target_self = target_dir / "signal-loom.yaml"
        others = [
            p for p in find_existing_configs(target_dir) if p.resolve() != target_self.resolve()
        ]
        if others:
            listed = "\n  ".join(str(p) for p in others)
            print(
                f"signal-loom: found existing config(s) under {target_dir}:\n  {listed}\n\n"
                "Refusing to scaffold a new config — this project looks already set up.\n"
                "  • To USE an existing config:  run the pipeline with "
                "`--config <one of the paths above>`\n"
                "  • To create a new one anyway: re-run with --force",
                file=sys.stderr,
            )
            return 1

    created, skipped = _write(template_dir, target_dir, force=args.force)

    if skipped:
        names = ", ".join(skipped)
        print(
            f"refusing to overwrite existing files in {target_dir}: {names}\n"
            f"re-run with --force to replace, or pick a different --to directory.",
            file=sys.stderr,
        )
        return 1

    if not created:
        print(
            f"template {args.template!r} contained no copyable files",
            file=sys.stderr,
        )
        return 1

    print(f"signal-loom: wrote {len(created)} config file(s) into {target_dir}")
    for name in created:
        print(f"  + {name}")
    print()
    print("Next steps:")
    print(f"  1. Edit {target_dir / 'sources.yaml'} to add your sources.")
    print(f"  2. Edit {target_dir / 'topics.yaml'} to set your controlled vocabulary.")
    print("  3. Run `python -m core.pipeline --once --no-enrich` to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
