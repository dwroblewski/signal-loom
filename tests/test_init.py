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


def test_init_root_config_keeps_output_inside_project(tmp_path):
    """Regression: a config scaffolded at the project ROOT must resolve its
    output dirs INSIDE the project, never escaping via `../`.

    Guards the path-skew bug where signal-loom.example.yaml hardcoded
    `content_dir: ../content`, which pointed outside the project when init
    wrote the config to a root directory.
    """
    from core import config as cfg

    import os

    assert init_mod.main(["--to", str(tmp_path)]) == 0
    settings = cfg.load_settings(tmp_path / "signal-loom.yaml")

    # os.path.realpath canonicalizes symlinks identically on both sides
    # (pytest's tmp_path may live under a symlinked TMPDIR on macOS, which made
    # naive Path.resolve() comparisons skew).
    root = os.path.realpath(tmp_path)
    content = os.path.realpath(settings.content_dir)
    index = os.path.realpath(settings.index_path)

    # Intent: output lands INSIDE the project, and the stored path never uses
    # a `..` segment that would escape the project root when init writes to root.
    assert ".." not in Path(settings.content_dir).parts
    assert ".." not in Path(settings.index_path).parts
    assert content == os.path.join(root, "content")
    assert index == os.path.join(root, "index.json")


def test_init_refuses_when_nested_config_exists(tmp_path, capsys):
    """init must NOT scaffold a duplicate when a config already exists in a
    nested/non-standard location the walk-up resolver can't find."""
    nested = tmp_path / "config" / "recipe-trends"
    nested.mkdir(parents=True)
    (nested / "signal-loom.yaml").write_text("enrichment_model: x\n")

    rc = init_mod.main(["--to", str(tmp_path)])

    assert rc != 0
    err = capsys.readouterr().err.lower()
    assert "existing" in err
    assert "--config" in err or "--force" in err
    # Crucially, it did NOT scaffold over the configured project.
    assert not (tmp_path / "signal-loom.yaml").exists()


def test_init_force_bypasses_existing_nested_config(tmp_path):
    """--force is the explicit escape hatch for the existing-config refusal."""
    nested = tmp_path / "config" / "recipe-trends"
    nested.mkdir(parents=True)
    (nested / "signal-loom.yaml").write_text("enrichment_model: x\n")

    rc = init_mod.main(["--to", str(tmp_path), "--force"])

    assert rc == 0
    assert (tmp_path / "signal-loom.yaml").exists()


def test_init_refuses_inside_configured_parent_project(tmp_path, monkeypatch):
    """Scaffolding in a SUBDIRECTORY of an already-configured project must refuse
    (the new config would shadow the parent's for the whole subtree)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "work"
    sub = project / "analysis"
    sub.mkdir(parents=True)
    (project / "signal-loom.yaml").write_text("enrichment_model: x\n")

    rc = init_mod.main(["--to", str(sub)])

    assert rc != 0
    assert not (sub / "signal-loom.yaml").exists()


def test_init_does_not_refuse_for_home_global_config(tmp_path, monkeypatch):
    """A home-global config (~/config/signal-loom.yaml dotfiles) is NOT a wrapping
    project — init in an unrelated project under $HOME must still succeed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "signal-loom.yaml").write_text("enrichment_model: x\n")
    project = tmp_path / "projects" / "newproj"
    project.mkdir(parents=True)

    rc = init_mod.main(["--to", str(project)])

    assert rc == 0
    assert (project / "signal-loom.yaml").exists()
