import subprocess, sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def test_check_only_exits_zero_when_venv_present(tmp_path):
    plugin_root = tmp_path / "plugin"
    venv_bin = plugin_root / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv_bin.mkdir(parents=True)
    (venv_bin / ("python.exe" if os.name == "nt" else "python")).touch()
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(plugin_root)}

    r = subprocess.run([sys.executable, str(ROOT/"hooks/scripts/bootstrap.py"), "--check-only"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
def test_errors_actionably_when_uv_missing(tmp_path):
    env = {**os.environ, "PATH": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(tmp_path)}  # empty PATH → no uv
    r = subprocess.run([sys.executable, str(ROOT/"hooks/scripts/bootstrap.py")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "uv" in r.stderr.lower() and "README" in r.stderr


def test_check_only_still_bootstraps_missing_configs(tmp_path):
    plugin_root = tmp_path / "plugin"
    config_dir = plugin_root / "config"
    config_dir.mkdir(parents=True)
    (plugin_root / ".venv" / ("Scripts" if os.name == "nt" else "bin")).mkdir(parents=True)
    (plugin_root / ".venv" / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")).touch()

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
    assert (config_dir / "signal-loom.yaml").exists()
    assert (config_dir / "sources.yaml").exists()
    assert (config_dir / "topics.yaml").exists()
    assert (config_dir / "entity-aliases.yaml").exists()
