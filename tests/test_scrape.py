"""tests/test_scrape.py — Tests for core.scrape adapters.

Tests confirm:
  - RSS adapter writes valid YAML frontmatter markdown (not line-1 hashtags)
  - keyword_filter drops off-topic items
  - YouTube captions adapter writes valid YAML frontmatter markdown
"""

import json
from pathlib import Path

from core import config, scrape

FX = Path(__file__).parent / "fixtures"


def test_rss_writes_frontmatter_markdown(tmp_path, rss_fixture):
    src = config.SourceConfig(
        name="X",
        type="rss",
        feed_url="f",
        output_dir=str(tmp_path),
        tags=["newsletter", "x"],
        scrape_limit=5,
    )
    written = scrape.run_source(
        src,
        fetch_feed=lambda u: rss_fixture,
        fetch_article=lambda u: "body text " * 60,
    )
    assert written, "Expected at least one file written"
    md = written[0].read_text()
    assert md.startswith("---"), "File must start with YAML frontmatter delimiter"
    assert "tags:" in md, "Frontmatter must contain tags key"
    assert not md.splitlines()[0].startswith("#"), "First line must not be a hashtag"


def test_keyword_filter_drops_offtopic(tmp_path, rss_fixture):
    src = config.SourceConfig(
        name="X",
        type="rss",
        feed_url="f",
        output_dir=str(tmp_path),
        tags=["x"],
        keyword_filter={"mode": "any", "include": ["zzzznomatch"]},
    )
    result = scrape.run_source(
        src,
        fetch_feed=lambda u: rss_fixture,
        fetch_article=lambda u: "body " * 60,
    )
    assert result == [], "All items should be filtered out by keyword_filter"


def test_youtube_captions_to_markdown(tmp_path):
    src = config.SourceConfig(
        name="Y",
        type="youtube",
        feed_url="https://yt/@x",
        output_dir=str(tmp_path),
        tags=["youtube"],
        scrape_limit=2,
    )
    caps = json.loads((FX / "yt_captions.json").read_text())
    written = scrape.run_source(src, fetch_youtube=lambda u, n: caps)
    assert written, "Expected at least one file written"
    assert written[0].read_text().startswith("---"), "YouTube file must start with YAML frontmatter"
