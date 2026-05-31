#!/usr/bin/env python3
"""SessionStart bootstrap: idempotent environment prep (``uv sync``) only.

Ensures the plugin's virtualenv exists so the first real ``uv run`` is fast.
It writes NO config files.

Why no config bootstrap
-----------------------
Earlier versions auto-copied ``*.example.yaml`` → ``*.yaml`` into the plugin's
own ``config/`` directory on every session. Under the v0.3.0 walk-up resolver
that behavior is wrong and harmful:

* Config now lives in the USER'S project and is created explicitly via
  ``python -m core.init``. The resolver never reads the plugin install's
  ``config/`` dir, so files written there are dead weight.
* For a local *Directory*-source marketplace, ``CLAUDE_PLUGIN_ROOT`` points at
  the developer's working tree, so the old hook scribbled untracked
  ``config/*.yaml`` straight into the source repo on every session start.
* It is exactly the "silent auto-bootstrap" v0.3.0 set out to remove; it had
  merely survived inside this hook.

``--check-only`` is accepted for hook backward-compatibility and now means what
it says: verify/prepare the environment, write nothing else. Cross-platform
(no .sh/.ps1 split).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_root() -> Path:
    """Plugin root: PLUGIN_ROOT (Codex) > CLAUDE_PLUGIN_ROOT (Claude) > inferred."""
    return Path(
        os.environ.get("PLUGIN_ROOT")
        or os.environ.get("CLAUDE_PLUGIN_ROOT")
        or Path(__file__).resolve().parents[2]
    )


def venv_python(root: Path) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"          # Windows path split
    exe = "python.exe" if os.name == "nt" else "python"
    return root / ".venv" / scripts / exe


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bootstrap.py",
        description="SessionStart environment prep for signal-loom (uv sync only; writes no config).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Accepted for hook compatibility; environment prep only, never writes config.",
    )
    parser.add_argument(
        "--print-root",
        action="store_true",
        help="Print the resolved plugin root and exit (diagnostic).",
    )
    args = parser.parse_args(argv)

    root = resolve_root()

    if args.print_root:
        print(root)
        return 0

    if shutil.which("uv") is None:
        sys.stderr.write(
            "signal-loom: `uv` not found on PATH. Install it "
            "(https://docs.astral.sh/uv/) — see README quickstart.\n"
        )
        return 1

    if not venv_python(root).exists():
        rc = subprocess.run(["uv", "sync"], cwd=root).returncode
        if rc != 0:
            return rc

    # Config files are intentionally NOT auto-created here. Scaffold them
    # explicitly in your project with: python -m core.init --to <dir>
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
