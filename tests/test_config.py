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
