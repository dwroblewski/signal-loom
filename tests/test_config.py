from pathlib import Path

from core import config
import pytest

# Anchor example-config paths to the repo root so these tests pass regardless of
# the directory pytest is invoked from (not just from the repo root).
ROOT = Path(__file__).resolve().parents[1]


def test_loads_example_sources():
    srcs = config.load_sources(str(ROOT / "config/sources.example.yaml"))
    assert all(s.type in {"rss", "youtube", "listing"} for s in srcs) and any(
        s.keyword_filter for s in srcs
    )


def test_rejects_unknown_type(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text("bad:\n  type: podcast\n  feed_url: x\n  output_dir: y\n")
    with pytest.raises(config.ConfigError) as e:
        config.load_sources(str(p))
    assert "podcast" in str(e.value) and "v1.1" in str(e.value)


def test_loads_topic_vocabulary():
    vocab = config.load_vocabulary(str(ROOT / "config/topics.example.yaml"))
    assert isinstance(vocab, set) and len(vocab) >= 3


def test_loads_aliases():
    aliases = config.load_aliases(str(ROOT / "config/entity-aliases.example.yaml"))
    assert isinstance(aliases, dict)


def test_settings_default_model():
    assert (
        config.load_settings(str(ROOT / "config/signal-loom.example.yaml")).enrichment_model
        == "claude-sonnet-4-6"
    )


# ---------------------------------------------------------------------------
# Fix #6 — output_dir containment check
# ---------------------------------------------------------------------------


def test_rejects_absolute_output_dir(tmp_path):
    """load_sources must raise ConfigError for an absolute output_dir."""
    p = tmp_path / "s.yaml"
    p.write_text(
        "bad:\n"
        "  type: rss\n"
        "  feed_url: https://example.com/feed\n"
        "  output_dir: /etc/x\n"
        "  enabled: true\n"
    )
    with pytest.raises(config.ConfigError, match="absolute"):
        config.load_sources(str(p))


def test_rejects_dotdot_output_dir(tmp_path):
    """load_sources must raise ConfigError when output_dir contains '..' segments."""
    p = tmp_path / "s.yaml"
    p.write_text(
        "bad:\n"
        "  type: rss\n"
        "  feed_url: https://example.com/feed\n"
        "  output_dir: ../escape\n"
        "  enabled: true\n"
    )
    with pytest.raises(config.ConfigError, match=r"\.\.|absolute|containment"):
        config.load_sources(str(p))


def test_rejects_nested_dotdot_output_dir(tmp_path):
    """load_sources must reject paths with '..' after normalization (e.g. a/../../etc)."""
    p = tmp_path / "s.yaml"
    p.write_text(
        "bad:\n"
        "  type: rss\n"
        "  feed_url: https://example.com/feed\n"
        "  output_dir: content/../../etc\n"
        "  enabled: true\n"
    )
    with pytest.raises(config.ConfigError):
        config.load_sources(str(p))


def test_accepts_relative_output_dir(tmp_path):
    """load_sources must accept a simple relative output_dir without '..'."""
    p = tmp_path / "s.yaml"
    p.write_text(
        "ok:\n"
        "  type: rss\n"
        "  feed_url: https://example.com/feed\n"
        "  output_dir: content/my-source\n"
        "  enabled: true\n"
    )
    sources = config.load_sources(str(p))
    assert sources and sources[0].output_dir == "content/my-source"


def test_config_named_project_root_keeps_output_inside(tmp_path, monkeypatch):
    """A project whose ROOT is literally named 'config' (e.g. a dotfiles repo
    scaffolded via `core.init --to ~/config`) must NOT get `../` defaults that
    escape it. The legacy `<project>/config/` layout still gets `../`."""
    import os
    home = tmp_path
    monkeypatch.setenv("HOME", str(home))
    cfgdir = home / "config"
    cfgdir.mkdir()
    (cfgdir / "signal-loom.yaml").write_text("enrichment_model: claude-sonnet-4-6\n")

    settings = config.load_settings(cfgdir / "signal-loom.yaml")

    assert ".." not in Path(settings.content_dir).parts
    assert ".." not in Path(settings.index_path).parts
    # Output resolves INSIDE ~/config, not ~/content.
    assert os.path.realpath(settings.content_dir) == os.path.realpath(cfgdir / "content")


def test_legacy_config_subdir_still_uses_parent_defaults(tmp_path, monkeypatch):
    """The legacy `<project>/config/signal-loom.yaml` layout keeps output beside
    the repo (../content), not buried in config/."""
    import os
    monkeypatch.setenv("HOME", str(tmp_path))
    project = tmp_path / "myproject"
    cfgdir = project / "config"
    cfgdir.mkdir(parents=True)
    (cfgdir / "signal-loom.yaml").write_text("enrichment_model: claude-sonnet-4-6\n")

    settings = config.load_settings(cfgdir / "signal-loom.yaml")

    assert os.path.realpath(settings.content_dir) == os.path.realpath(project / "content")
