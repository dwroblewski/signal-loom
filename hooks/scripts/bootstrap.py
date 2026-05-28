#!/usr/bin/env python3
"""SessionStart bootstrap: idempotent `uv sync`. Cross-platform (no .sh/.ps1 split)."""
import os, shutil, subprocess, sys
from pathlib import Path
ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT", Path(__file__).resolve().parents[2]))
def venv_python(root: Path) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"          # Windows path split
    exe = "python.exe" if os.name == "nt" else "python"
    return root / ".venv" / scripts / exe
def main() -> int:
    if "--check-only" in sys.argv and venv_python(ROOT).exists():
        return 0
    if shutil.which("uv") is None:
        sys.stderr.write("signal-loom: `uv` not found on PATH. Install it "
                         "(https://docs.astral.sh/uv/) — see README quickstart.\n")
        return 1
    if venv_python(ROOT).exists():
        return 0
    return subprocess.run(["uv", "sync"], cwd=ROOT).returncode
if __name__ == "__main__":
    raise SystemExit(main())
