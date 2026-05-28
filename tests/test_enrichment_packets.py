import json
import os
import subprocess
import sys
from pathlib import Path

import frontmatter

from core import enrichment_packets


ROOT = Path(__file__).resolve().parents[1]


def _make_config(tmp_path: Path) -> Path:
    content = tmp_path / "content"
    content.mkdir()
    topics = tmp_path / "topics.yaml"
    topics.write_text("- ai agents\n- model releases\n")
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("{}\n")
    cfg = tmp_path / "signal-loom.yaml"
    cfg.write_text(
        f"content_dir: {content}\n"
        f"index_path: {tmp_path / 'index.json'}\n"
        f"topics_path: {topics}\n"
        f"aliases_path: {aliases}\n"
        f"sources_path: {tmp_path / 'sources.yaml'}\n"
    )
    (tmp_path / "sources.yaml").write_text("{}\n")
    return cfg


def _write_md(path: Path, *, enriched: bool = False) -> None:
    post = frontmatter.Post(
        "This article body discusses AI agents and model releases.",
        title=path.stem,
        enriched=enriched,
    )
    path.write_text(frontmatter.dumps(post))


def test_build_packets_emits_only_unenriched_files(tmp_path):
    cfg = _make_config(tmp_path)
    content = tmp_path / "content"
    raw = content / "raw.md"
    done = content / "done.md"
    _write_md(raw)
    _write_md(done, enriched=True)

    packets = enrichment_packets.build_packets(config=str(cfg))

    assert len(packets) == 1
    assert packets[0]["path"] == str(raw.resolve())
    assert "Allowed primary topics: ai agents, model releases" in packets[0]["prompt"]
    assert packets[0]["output_contract"].startswith("Return only")


def test_build_packets_respects_limit(tmp_path):
    cfg = _make_config(tmp_path)
    content = tmp_path / "content"
    for name in ["b.md", "a.md", "c.md"]:
        _write_md(content / name)

    packets = enrichment_packets.build_packets(config=str(cfg), limit=2)

    assert [Path(packet["path"]).name for packet in packets] == ["a.md", "b.md"]


def test_enrichment_packets_cli_does_not_require_api_keys(tmp_path):
    cfg = _make_config(tmp_path)
    _write_md(tmp_path / "content" / "raw.md")
    out = tmp_path / "packets.jsonl"
    env = {
        **os.environ,
        "ANTHROPIC_API_KEY": "",
        "OPENAI_API_KEY": "",
        "CODEX_API_KEY": "",
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "core.enrichment_packets",
            "emit",
            "--config",
            str(cfg),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["path"].endswith("raw.md")
