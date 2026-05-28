#!/usr/bin/env python3
"""SessionStart bootstrap: idempotent `uv sync` + config auto-bootstrap.

After uv sync completes, copies *.example.yaml → *.yaml for any missing
config files so a fresh plugin session has real config files to edit.
Cross-platform (no .sh/.ps1 split).
"""
import os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[2]))


def venv_python(root: Path) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"          # Windows path split
    exe = "python.exe" if os.name == "nt" else "python"
    return root / ".venv" / scripts / exe


def bootstrap_configs(root: Path) -> None:
    """Copy missing *.yaml from *.example.yaml in the config directory."""
    config_dir = root / "config"
    if not config_dir.exists():
        return
    bases = ["signal-loom", "sources", "topics", "entity-aliases"]
    for base in bases:
        target = config_dir / f"{base}.yaml"
        example = config_dir / f"{base}.example.yaml"
        if not target.exists() and example.exists():
            shutil.copy2(example, target)
            sys.stderr.write(
                f"signal-loom: created {target.name} from example — "
                f"edit it to add your sources/topics\n"
            )


def main() -> int:
    if "--check-only" in sys.argv and venv_python(ROOT).exists():
        return 0
    if shutil.which("uv") is None:
        sys.stderr.write("signal-loom: `uv` not found on PATH. Install it "
                         "(https://docs.astral.sh/uv/) — see README quickstart.\n")
        return 1
    if not venv_python(ROOT).exists():
        rc = subprocess.run(["uv", "sync"], cwd=ROOT).returncode
        if rc != 0:
            return rc
    # Always run config bootstrap (idempotent — only creates missing files)
    bootstrap_configs(ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
