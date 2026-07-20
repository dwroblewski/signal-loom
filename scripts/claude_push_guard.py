#!/usr/bin/env python3
"""Claude Code PreToolUse guard — block Claude from `git push`-ing secrets.

Registered in .claude/settings.json on the Bash tool. Reads the hook JSON on
stdin; acts ONLY on `git push` commands. Scans the commits about to be pushed
via scripts/check-leaks.sh. Exit 2 blocks the tool call and shows stderr to
Claude (so the agent must resolve the finding before pushing); exit 0 allows it.

This is the agent-specific layer — it enforces "audit before push" mechanically
rather than relying on the model remembering. The native git pre-push hook covers
manual pushes; GitHub push protection is the server-side net.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _rev(root: str, expr: str) -> str:
    r = _run(["git", "-C", root, "rev-parse", "--verify", "--quiet", expr])
    return r.stdout.strip() if r.returncode == 0 else ""


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0  # unparseable input -> don't interfere

    if data.get("tool_name") != "Bash":
        return 0
    command = (data.get("tool_input") or {}).get("command", "") or ""
    if not re.search(r"\bgit\s+push\b", command):
        return 0

    root_res = _run(["git", "rev-parse", "--show-toplevel"])
    if root_res.returncode != 0:
        return 0
    root = root_res.stdout.strip()

    head = _rev(root, "HEAD")
    if not head:
        return 0

    # Range about to be pushed: upstream..HEAD, else origin/main..HEAD, else HEAD.
    base = ""
    up = _run(["git", "-C", root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if up.returncode == 0 and up.stdout.strip():
        base = _rev(root, up.stdout.strip())
    if not base:
        base = _rev(root, "origin/main")
    rng = f"{base}..{head}" if base else head

    script = os.path.join(root, "scripts", "check-leaks.sh")
    res = _run([script, "range", rng])
    if res.returncode != 0:
        sys.stderr.write(
            "Blocked `git push` — leak-guard found a problem in the outgoing commits:\n\n"
            + (res.stderr or "")
            + (res.stdout or "")
            + "\nThis is a PUBLIC repo. Resolve the finding (or amend/rewrite the commit) "
            "before pushing. Do not bypass with --no-verify without confirming with the user.\n"
        )
        return 2  # block
    return 0


if __name__ == "__main__":
    sys.exit(main())
