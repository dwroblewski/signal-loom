"""CI gate test: verify that recorded real-LLM output passes validate.check and writeback.

Runs OFFLINE using the pre-recorded fixture at tests/fixtures/recorded_enrichment.json.
No API key is required — the fixture was captured once via tests/record_enrichment.py.
"""

import json
from pathlib import Path

from core import enrichment_writeback as wb, config


def test_recorded_real_output_passes_validation(tmp_path):
    raw = json.loads(
        (Path(__file__).parent / "fixtures" / "recorded_enrichment.json").read_text()
    )["text"]
    vocab = config.load_vocabulary("config/topics.example.yaml")
    f = tmp_path / "a.md"
    f.write_text("---\ntitle: T\n---\nbody")
    res = wb.apply(f, raw, vocabulary=vocab, aliases={})
    assert res.ok, f"real model output failed validation: {res.errors}"
