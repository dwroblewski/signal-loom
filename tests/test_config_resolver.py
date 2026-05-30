"""Tests for the new config resolver — precedence + walk-up + missing-config error.

The old resolver (see test_onboarding_hardening.py for the legacy tests we
intentionally supersede) silently fell back to the plugin's bundled
PACKAGE_CONFIG_DIR and auto-copied *.example.yaml on every run. That was the
antipattern the rewrite removes.

New precedence (first existing file wins, otherwise raises):
  1. explicit (CLI flag)
  2. $CLAUDE_PLUGIN_OPTION_CONFIG_PATH (Claude Code userConfig output)
  3. $SIGNAL_LOOM_CONFIG (legacy; resolver emits DeprecationWarning)
  4. Walk up from project_dir / $CLAUDE_PROJECT_DIR / cwd looking for:
       signal-loom.yaml
       .signal-loom.yaml
       .signal-loom/config.yaml
       config/signal-loom.yaml      (legacy layout)
     Stop at $HOME or filesystem root.
  5. ConfigNotFoundError with an actionable message.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import config as cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every resolver test starts from a clean env to avoid host-machine leakage."""
    for var in (
        "SIGNAL_LOOM_CONFIG",
        "CLAUDE_PLUGIN_OPTION_CONFIG_PATH",
        "CLAUDE_PROJECT_DIR",
    ):
        monkeypatch.delenv(var, raising=False)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("enrichment_model: claude-sonnet-4-6\n")
    return path


# ---------------------------------------------------------------------------
# (1) explicit --config
# ---------------------------------------------------------------------------


def test_explicit_path_returned(tmp_path):
    p = _touch(tmp_path / "my.yaml")
    assert cfg.resolve_config_path(str(p)) == p


def test_explicit_missing_raises(tmp_path):
    """An explicitly-passed --config that does not exist is a hard error."""
    p = tmp_path / "does-not-exist.yaml"
    with pytest.raises(cfg.ConfigNotFoundError) as exc:
        cfg.resolve_config_path(str(p))
    assert str(p) in str(exc.value)


# ---------------------------------------------------------------------------
# (2) $CLAUDE_PLUGIN_OPTION_CONFIG_PATH
# ---------------------------------------------------------------------------


def test_claude_user_config_env_honored(tmp_path, monkeypatch):
    p = _touch(tmp_path / "from-userconfig.yaml")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CONFIG_PATH", str(p))
    assert cfg.resolve_config_path(None) == p


def test_claude_user_config_missing_falls_through(tmp_path, monkeypatch):
    """If the env points at a non-existent file, fall through to lower-precedence layers."""
    discovered = _touch(tmp_path / "signal-loom.yaml")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CONFIG_PATH", str(tmp_path / "missing.yaml"))
    assert cfg.resolve_config_path(None, cwd=tmp_path) == discovered


# ---------------------------------------------------------------------------
# (3) $SIGNAL_LOOM_CONFIG (legacy; deprecation warning)
# ---------------------------------------------------------------------------


def test_legacy_env_var_still_honored(tmp_path, monkeypatch):
    p = _touch(tmp_path / "legacy.yaml")
    monkeypatch.setenv("SIGNAL_LOOM_CONFIG", str(p))
    with pytest.warns(DeprecationWarning, match="SIGNAL_LOOM_CONFIG"):
        result = cfg.resolve_config_path(None)
    assert result == p


def test_userconfig_beats_legacy_env(tmp_path, monkeypatch):
    """When both are set, $CLAUDE_PLUGIN_OPTION_CONFIG_PATH wins (no warning)."""
    user = _touch(tmp_path / "user.yaml")
    legacy = _touch(tmp_path / "legacy.yaml")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CONFIG_PATH", str(user))
    monkeypatch.setenv("SIGNAL_LOOM_CONFIG", str(legacy))
    # No warning emitted because legacy env is never read.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert cfg.resolve_config_path(None) == user


# ---------------------------------------------------------------------------
# (4) Walk-up discovery
# ---------------------------------------------------------------------------


def test_walkup_finds_signal_loom_yaml_in_cwd(tmp_path):
    p = _touch(tmp_path / "signal-loom.yaml")
    assert cfg.resolve_config_path(None, cwd=tmp_path) == p


def test_walkup_finds_dot_signal_loom_yaml(tmp_path):
    p = _touch(tmp_path / ".signal-loom.yaml")
    assert cfg.resolve_config_path(None, cwd=tmp_path) == p


def test_walkup_finds_dot_signal_loom_dir(tmp_path):
    p = _touch(tmp_path / ".signal-loom" / "config.yaml")
    assert cfg.resolve_config_path(None, cwd=tmp_path) == p


def test_walkup_finds_legacy_config_layout(tmp_path):
    """Existing repos using config/signal-loom.yaml keep working."""
    p = _touch(tmp_path / "config" / "signal-loom.yaml")
    assert cfg.resolve_config_path(None, cwd=tmp_path) == p


def test_walkup_searches_parent_directories(tmp_path):
    p = _touch(tmp_path / "signal-loom.yaml")
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    assert cfg.resolve_config_path(None, cwd=nested) == p


def test_walkup_closest_wins(tmp_path):
    """Closer signal-loom.yaml shadows the one higher up."""
    far = _touch(tmp_path / "signal-loom.yaml")
    near_dir = tmp_path / "sub"
    near = _touch(near_dir / "signal-loom.yaml")
    assert cfg.resolve_config_path(None, cwd=near_dir) == near
    assert far.exists()  # untouched


def test_walkup_filename_precedence(tmp_path):
    """In one directory: signal-loom.yaml > .signal-loom.yaml > .signal-loom/config.yaml > config/signal-loom.yaml."""
    primary = _touch(tmp_path / "signal-loom.yaml")
    _touch(tmp_path / ".signal-loom.yaml")
    _touch(tmp_path / ".signal-loom" / "config.yaml")
    _touch(tmp_path / "config" / "signal-loom.yaml")
    assert cfg.resolve_config_path(None, cwd=tmp_path) == primary


def test_walkup_uses_claude_project_dir_env(tmp_path, monkeypatch):
    """$CLAUDE_PROJECT_DIR is preferred over cwd as the walk-up start."""
    project = tmp_path / "project"
    project.mkdir()
    p = _touch(project / "signal-loom.yaml")
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    assert cfg.resolve_config_path(None, cwd=other_cwd) == p


def test_walkup_stops_at_home(tmp_path, monkeypatch):
    """Walk-up must not escape $HOME or it could read another user's config."""
    home = tmp_path / "home"
    home.mkdir()
    # Drop a config ABOVE $HOME — must not be found.
    above_home = _touch(tmp_path / "signal-loom.yaml")
    start = home / "project"
    start.mkdir()
    monkeypatch.setenv("HOME", str(home))
    with pytest.raises(cfg.ConfigNotFoundError):
        cfg.resolve_config_path(None, cwd=start)
    assert above_home.exists()


# ---------------------------------------------------------------------------
# (5) Missing config — actionable error
# ---------------------------------------------------------------------------


def test_missing_config_raises_with_actionable_message(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))  # bound the walk-up
    start = tmp_path / "project"
    start.mkdir()
    with pytest.raises(cfg.ConfigNotFoundError) as exc:
        cfg.resolve_config_path(None, cwd=start)
    msg = str(exc.value)
    # Must show what was searched and how to fix.
    assert "signal-loom.yaml" in msg
    assert "init" in msg.lower()


# ---------------------------------------------------------------------------
# (6) Back-compat: legacy callsites passing positional `explicit`
# ---------------------------------------------------------------------------


def test_legacy_positional_explicit_argument(tmp_path):
    """Existing callers do resolve_config_path(args.config) — must still work."""
    p = _touch(tmp_path / "explicit.yaml")
    assert cfg.resolve_config_path(str(p)) == p
