#!/usr/bin/env python3
"""Run a real Codex plugin e2e against a temporary local marketplace.

The harness installs this checkout as a Codex plugin, runs Codex with plugin
skills enabled, asks Codex to use the installed signal-loom ``$enrich`` skill,
and verifies frontmatter plus index output from the installed plugin cache.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import frontmatter


FORBIDDEN_ENV_KEYS = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY")
PLUGIN_NAME = "signal-loom"
DEFAULT_MARKETPLACE = "signal-loom-real-e2e"
E2E_ARTICLE_REL = Path("content/e2e/real-codex-e2e.md")
E2E_INDEX_REL = "e2e/real-codex-e2e.md"


class E2EError(RuntimeError):
    """Raised when the real Codex e2e cannot prove the expected boundary."""


def scrub_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return *env* without API-key variables that would mask account auth."""
    base = dict(os.environ if env is None else env)
    for key in FORBIDDEN_ENV_KEYS:
        base.pop(key, None)
    return base


def marketplace_payload(marketplace_name: str) -> dict[str, Any]:
    """Return the local marketplace JSON used by this harness."""
    return {
        "name": marketplace_name,
        "interface": {"displayName": "Signal Loom Real E2E"},
        "owner": {"name": "local"},
        "description": "Temporary signal-loom Codex e2e marketplace.",
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
                "description": "Signal Loom local Codex e2e install.",
            }
        ],
    }


def write_marketplace(root: Path, repo_root: Path, marketplace_name: str) -> Path:
    """Create a temporary local marketplace pointing at *repo_root*."""
    marketplace_file = root / ".agents/plugins/marketplace.json"
    marketplace_file.parent.mkdir(parents=True, exist_ok=True)
    plugins_dir = root / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugin_link = plugins_dir / PLUGIN_NAME
    plugin_link.symlink_to(repo_root, target_is_directory=True)
    marketplace_file.write_text(
        json.dumps(marketplace_payload(marketplace_name), indent=2) + "\n",
        encoding="utf-8",
    )
    return marketplace_file


def run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and raise with captured output on failure."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise E2EError(
            "command timed out: "
            + " ".join(args)
            + f"\nstdout:\n{exc.stdout or ''}\nstderr:\n{exc.stderr or ''}"
        ) from exc
    if check and result.returncode != 0:
        raise E2EError(
            "command failed: "
            + " ".join(args)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def parse_install_root(output: str) -> Path:
    """Extract the installed plugin cache root from ``codex plugin add`` output."""
    match = re.search(r"Installed plugin root:\s*(.+)", output)
    if not match:
        raise E2EError(f"could not parse installed plugin root from:\n{output}")
    return Path(match.group(1).strip())


def codex_exec_args(output_file: Path, cwd: Path, prompt: str) -> list[str]:
    """Return the common Codex exec invocation used by the e2e.

    Codex >=0.135 defaults ``codex exec`` to the read-only sandbox, which blocks
    the deterministic enrichment writes (raw YAML in the temp dir, config
    bootstrap, frontmatter writeback, index rebuild inside the installed plugin
    cache). Run under ``workspace-write`` and grant the two roots outside the
    primary workspace (``-C cwd``): the temp dir (raw YAML) and the Codex plugin
    cache (installed plugin root). This stays well short of full disk access.
    """
    writable_roots = [tempfile.gettempdir(), str(Path.home() / ".codex/plugins")]
    add_dir_args: list[str] = []
    for root in writable_roots:
        add_dir_args += ["--add-dir", root]
    return [
        "codex",
        "exec",
        "--enable",
        "plugins",
        "--enable",
        "hooks",
        "--dangerously-bypass-hook-trust",
        "--sandbox",
        "workspace-write",
        *add_dir_args,
        "-c",
        'forced_login_method="chatgpt"',
        "-c",
        "allow_login_shell=false",
        "-c",
        "shell_environment_policy.experimental_use_profile=false",
        "-c",
        'shell_environment_policy.exclude=["OPENAI_API_KEY","CODEX_API_KEY","ANTHROPIC_API_KEY"]',
        "--output-last-message",
        str(output_file),
        "-C",
        str(cwd),
        prompt,
    ]


def clean_codex_env(zdotdir: Path) -> dict[str, str]:
    """Return a Codex process env with API keys removed and zsh startup isolated."""
    env = scrub_env()
    env["ZDOTDIR"] = str(zdotdir)
    return env


def expected_config_files(install_root: Path) -> list[Path]:
    """Return config files that hook or lazy bootstrap should create."""
    return [
        install_root / "config/signal-loom.yaml",
        install_root / "config/sources.yaml",
        install_root / "config/topics.yaml",
        install_root / "config/entity-aliases.yaml",
    ]


def remove_bootstrapped_configs(install_root: Path) -> None:
    """Remove generated config files so hook bootstrap can be observed."""
    for path in expected_config_files(install_root):
        path.unlink(missing_ok=True)


def hook_bootstrap_observed(install_root: Path) -> bool:
    """Return True when all expected config files exist."""
    return all(path.exists() for path in expected_config_files(install_root))


def write_fixture_article(install_root: Path) -> Path:
    """Plant one unenriched article in the installed plugin cache."""
    article = install_root / E2E_ARTICLE_REL
    article.parent.mkdir(parents=True, exist_ok=True)
    article.write_text(
        """---
title: Real Codex E2E Signal Loom Enrichment Test
source: Signal Loom E2E Harness
url: https://example.com/signal-loom/real-codex-e2e
published: 2026-05-28
tags:
  - ai
  - agents
  - enterprise
---

AI agents are moving from isolated demos into daily enterprise workflows as
teams connect them to source control, ticket queues, internal knowledge bases,
and approval systems. The most useful deployments pair autonomous task handling
with clear audit trails, scoped permissions, and human review points.

Recent model releases have improved tool use, long-context reasoning, coding
reliability, and multimodal understanding. Those gains make agents more useful
for software maintenance, customer operations, research synthesis, and data
analysis, but they also raise expectations for evaluation, rollback, and
security controls.

Enterprise adoption is therefore less about a single model launch and more
about turning model capability into governed systems. Buyers want measurable
productivity, predictable cost, integration with existing platforms, and a path
from pilot projects to broad deployment without losing compliance visibility.
""",
        encoding="utf-8",
    )
    return article


def build_enrich_prompt(install_root: Path, article: Path, raw_file: Path) -> str:
    """Build the Codex prompt that invokes the installed enrichment skill."""
    return f"""
Use the installed signal-loom plugin's `$enrich` skill for this real e2e.

Hard requirements:
- Work against this installed plugin root only: `{install_root}`.
- Enrich exactly this article: `{article}`.
- Do not read `~/.codex/auth.json`.
- Do not call OpenAI or Anthropic APIs from Python.
- Do not run `core.enrich.ApiEnricher`.
- Follow the Codex-native `$enrich` instructions, not the Claude-oriented
  top-level `skills/enrich` instructions.
- Before any signal-loom Python command, use a guarded child shell:
  `ROOT="{install_root}" env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY /bin/sh -c '...'`
- First verify the guarded child shell reports all three forbidden env vars absent.
- Generate one packet with `core.enrichment_packets emit --max-files 1`.
- Complete the packet prompt yourself as the active Codex session.
- Write the exact raw fenced YAML response to `{raw_file}` with a quoted here-doc or another method that does not interpolate model output.
- Apply it through `core.enrichment_writeback apply` with `--raw-file`.
- Rebuild `core.index`.
- Verify frontmatter `enriched: true` and one index entry for `{E2E_INDEX_REL}`.

Final response must include these keys, one per line:
skill_used: enrich
guarded_env: absent
writeback: ok
index_entry: ok
""".strip()


def verify_install_outputs(install_root: Path) -> dict[str, Any]:
    """Verify final frontmatter and index state in the installed plugin root."""
    article = install_root / E2E_ARTICLE_REL
    index_path = install_root / "index.json"
    if not article.exists():
        raise E2EError(f"missing e2e article: {article}")
    if not index_path.exists():
        raise E2EError(f"missing index: {index_path}")

    post = frontmatter.load(str(article))
    data = json.loads(index_path.read_text(encoding="utf-8"))
    matches = [
        entry
        for entry in data.get("entries", [])
        if entry.get("path") == E2E_INDEX_REL
    ]
    if post.metadata.get("enriched") is not True:
        raise E2EError("frontmatter enriched flag was not true")
    if len(matches) != 1:
        raise E2EError(f"expected one index match for {E2E_INDEX_REL}, got {len(matches)}")
    if matches[0].get("enriched") is not True:
        raise E2EError("index entry enriched flag was not true")
    return {
        "frontmatter_enriched": post.metadata.get("enriched"),
        "index_matches": len(matches),
        "index_enriched": matches[0].get("enriched"),
        "index_primary_topics": matches[0].get("topics", {}).get("primary", []),
    }


def verify_codex_final(output_file: Path) -> dict[str, Any]:
    """Verify the Codex final message reports the required e2e checkpoints."""
    text = output_file.read_text(encoding="utf-8")
    required = {
        "skill_used": "skill_used: enrich",
        "guarded_env": "guarded_env: absent",
        "writeback": "writeback: ok",
        "index_entry": "index_entry: ok",
    }
    missing = [name for name, marker in required.items() if marker not in text]
    if missing:
        raise E2EError(f"Codex final message missing checkpoint(s): {', '.join(missing)}")
    return {"codex_final_checkpoints": sorted(required)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="signal-loom checkout to install as a local Codex plugin",
    )
    parser.add_argument("--marketplace-name", default=DEFAULT_MARKETPLACE)
    parser.add_argument("--keep", action="store_true", help="keep temp marketplace/cache for debugging")
    parser.add_argument(
        "--require-hook",
        action="store_true",
        help="fail if Codex SessionStart plugin hooks do not bootstrap config files",
    )
    parser.add_argument(
        "--codex-timeout",
        type=int,
        default=600,
        help="timeout in seconds for each codex exec phase",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    if not (repo_root / ".codex-plugin/plugin.json").exists():
        raise SystemExit(f"not a signal-loom repo root: {repo_root}")
    if shutil.which("codex") is None:
        raise SystemExit("codex CLI not found on PATH")

    marketplace_root = Path(tempfile.mkdtemp(prefix=f"{args.marketplace_name}-marketplace-"))
    zdotdir = Path(tempfile.mkdtemp(prefix=f"{args.marketplace_name}-zdotdir-"))
    raw_file = Path(tempfile.gettempdir()) / f"{args.marketplace_name}-raw.yaml"
    hook_output = Path(tempfile.gettempdir()) / f"{args.marketplace_name}-hook.txt"
    enrich_output = Path(tempfile.gettempdir()) / f"{args.marketplace_name}-enrich.txt"
    installed_root: Path | None = None
    cleanup_cache = Path.home() / ".codex/plugins/cache" / args.marketplace_name

    env = clean_codex_env(zdotdir)
    summary: dict[str, Any] = {"marketplace": args.marketplace_name}

    try:
        write_marketplace(marketplace_root, repo_root, args.marketplace_name)
        run(["codex", "plugin", "marketplace", "remove", args.marketplace_name], env=env, check=False)
        run(["codex", "plugin", "remove", f"{PLUGIN_NAME}@{args.marketplace_name}"], env=env, check=False)
        run(["codex", "plugin", "marketplace", "add", str(marketplace_root)], env=env)
        add_result = run(["codex", "plugin", "add", f"{PLUGIN_NAME}@{args.marketplace_name}"], env=env)
        installed_root = parse_install_root(add_result.stdout + add_result.stderr)
        summary["installed_root"] = str(installed_root)

        remove_bootstrapped_configs(installed_root)
        hook_prompt = "Do not run any shell commands. Reply exactly: hook smoke done."
        run(codex_exec_args(hook_output, repo_root, hook_prompt), env=env, timeout=args.codex_timeout)
        hook_ok = hook_bootstrap_observed(installed_root)
        summary["hook_bootstrap"] = hook_ok
        if args.require_hook and not hook_ok:
            raise E2EError("Codex plugin SessionStart hook did not bootstrap config files")

        article = write_fixture_article(installed_root)
        prompt = build_enrich_prompt(installed_root, article, raw_file)
        run(codex_exec_args(enrich_output, repo_root, prompt), env=env, timeout=args.codex_timeout)
        summary.update(verify_codex_final(enrich_output))
        summary.update(verify_install_outputs(installed_root))
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        if not args.keep:
            run(["codex", "plugin", "remove", f"{PLUGIN_NAME}@{args.marketplace_name}"], env=env, check=False)
            run(["codex", "plugin", "marketplace", "remove", args.marketplace_name], env=env, check=False)
            shutil.rmtree(marketplace_root, ignore_errors=True)
            shutil.rmtree(zdotdir, ignore_errors=True)
            if cleanup_cache.exists():
                shutil.rmtree(cleanup_cache, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
