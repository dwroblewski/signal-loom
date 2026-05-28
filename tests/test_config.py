from core import config
import pytest


def test_loads_example_sources():
    srcs = config.load_sources("config/sources.example.yaml")
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
    vocab = config.load_vocabulary("config/topics.example.yaml")
    assert isinstance(vocab, set) and len(vocab) >= 3


def test_loads_aliases():
    aliases = config.load_aliases("config/entity-aliases.example.yaml")
    assert isinstance(aliases, dict)


def test_settings_default_model():
    assert (
        config.load_settings("config/signal-loom.example.yaml").enrichment_model
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
