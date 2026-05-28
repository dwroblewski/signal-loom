import subprocess, sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def test_check_only_exits_zero_when_venv_present():
    r = subprocess.run([sys.executable, str(ROOT/"hooks/scripts/bootstrap.py"), "--check-only"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
def test_errors_actionably_when_uv_missing(tmp_path):
    env = {**os.environ, "PATH": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(tmp_path)}  # empty PATH → no uv
    r = subprocess.run([sys.executable, str(ROOT/"hooks/scripts/bootstrap.py")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "uv" in r.stderr.lower() and "README" in r.stderr
