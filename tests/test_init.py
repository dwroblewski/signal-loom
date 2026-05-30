"""Tests for `python -m core.init` — the explicit config scaffold command.

Replaces the silent ensure_configs-on-run pattern with an explicit one-shot
that lands config files in the user's project directory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import init as init_mod


def test_init_writes_signal_loom_yaml(tmp_path):
    """Default: writes signal-loom.yaml to the target directory."""
    rc = init_mod.main(["--to", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "signal-loom.yaml").exists()
    assert (tmp_path / "sources.yaml").exists()
    assert (tmp_path / "topics.yaml").exists()
    assert (tmp_path / "entity-aliases.yaml").exists()


def test_init_refuses_to_overwrite(tmp_path, capsys):
    """Without --force, an existing signal-loom.yaml is left alone and the command errors."""
    (tmp_path / "signal-loom.yaml").write_text("# pre-existing\n")
    rc = init_mod.main(["--to", str(tmp_path)])
    assert rc != 0
    out = capsys.readouterr()
    combined = (out.err + out.out).lower()
    assert "exist" in combined or "overwrite" in combined
    # Pre-existing content untouched.
    assert (tmp_path / "signal-loom.yaml").read_text() == "# pre-existing\n"


def test_init_force_overwrites(tmp_path):
    """--force lets the user blow away their config (e.g. to re-init from a different template)."""
    (tmp_path / "signal-loom.yaml").write_text("# pre-existing\n")
    rc = init_mod.main(["--to", str(tmp_path), "--force"])
    assert rc == 0
    assert (tmp_path / "signal-loom.yaml").read_text() != "# pre-existing\n"


def test_init_resolver_finds_freshly_written_config(tmp_path):
    """End-to-end: init then resolver must discover the new config via walk-up."""
    from core import config as cfg

    assert init_mod.main(["--to", str(tmp_path)]) == 0
    found = cfg.resolve_config_path(None, cwd=tmp_path)
    assert found == (tmp_path / "signal-loom.yaml")


def test_init_template_unknown_errors(tmp_path, capsys):
    rc = init_mod.main(["--to", str(tmp_path), "--template", "does-not-exist"])
    assert rc != 0
    out = capsys.readouterr()
    assert "template" in (out.err + out.out).lower()
