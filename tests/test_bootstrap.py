import subprocess, sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def _venv(plugin_root: Path) -> None:
    """Create a fake venv python so bootstrap skips `uv sync`."""
    venv_bin = plugin_root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_bin.mkdir(parents=True)
    (venv_bin / ("python.exe" if os.name == "nt" else "python")).touch()


def test_check_only_exits_zero_when_venv_present(tmp_path):
    plugin_root = tmp_path / "plugin"
    _venv(plugin_root)
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(plugin_root)}

    r = subprocess.run([sys.executable, str(ROOT/"hooks/scripts/bootstrap.py"), "--check-only"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr


def test_check_only_accepts_codex_plugin_root(tmp_path):
    plugin_root = tmp_path / "plugin"
    _venv(plugin_root)
    env = {**os.environ, "PLUGIN_ROOT": str(plugin_root)}

    r = subprocess.run(
        [sys.executable, str(ROOT / "hooks/scripts/bootstrap.py"), "--check-only"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert r.returncode == 0, r.stderr


def test_codex_plugin_root_takes_precedence_over_claude_root(tmp_path):
    """PLUGIN_ROOT (Codex) must win over CLAUDE_PLUGIN_ROOT (Claude).

    Verified via --print-root rather than a config-write side effect, because
    the SessionStart hook no longer writes config files.
    """
    codex_root = tmp_path / "codex-plugin"
    claude_root = tmp_path / "claude-plugin"
    for plugin_root in (codex_root, claude_root):
        _venv(plugin_root)

    env = {
        **os.environ,
        "PLUGIN_ROOT": str(codex_root),
        "CLAUDE_PLUGIN_ROOT": str(claude_root),
    }
    r = subprocess.run(
        [sys.executable, str(ROOT / "hooks/scripts/bootstrap.py"), "--print-root"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(codex_root)


def test_errors_actionably_when_uv_missing(tmp_path):
    env = {**os.environ, "PATH": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(tmp_path)}  # empty PATH → no uv
    r = subprocess.run([sys.executable, str(ROOT/"hooks/scripts/bootstrap.py")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "uv" in r.stderr.lower() and "README" in r.stderr


def test_session_start_does_not_create_configs(tmp_path):
    """Regression: the SessionStart hook must NOT auto-create config files.

    Under the v0.3.0 walk-up resolver, config belongs in the user's project
    (created via `core.init`), never in the plugin install. For a local
    Directory-source marketplace, auto-writing here scribbled untracked
    config/*.yaml into the developer's source repo every session.
    """
    plugin_root = tmp_path / "plugin"
    config_dir = plugin_root / "config"
    config_dir.mkdir(parents=True)
    _venv(plugin_root)

    # Seed the example files exactly as they ship.
    for example in (ROOT / "config").glob("*.example.yaml"):
        (config_dir / example.name).write_text(example.read_text())

    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(plugin_root)}
    r = subprocess.run(
        [sys.executable, str(ROOT / "hooks/scripts/bootstrap.py"), "--check-only"],
        capture_output=True,
        text=True,
        env=env,
    )

    assert r.returncode == 0, r.stderr
    # No non-example config files were created.
    for base in ("signal-loom", "sources", "topics", "entity-aliases"):
        assert not (config_dir / f"{base}.yaml").exists(), (
            f"{base}.yaml was created by the hook — auto-bootstrap regression"
        )
