"""End-to-end contract the whole pipeline must satisfy.

Stays SKIPPED until core.pipeline exists (Task D3), at which point it goes
green and becomes the integration gate proving config→scrape→enrich→
writeback→index seams line up. See plan Task B0.
"""
import json
import pytest


@pytest.mark.skeleton
def test_endtoend_contract(tmp_path):
    pytest.importorskip("core.pipeline")  # skips until D3
    from core import pipeline

    rc = pipeline.main([
        "--config", _stub_config(tmp_path),
        "--once",
        "--_inject-fetch", "fixture",
        "--_inject-enricher", "fake",
    ])
    assert rc == 0
    idx = json.loads((tmp_path / "index.json").read_text())
    assert idx["entries"] and idx["entries"][0]["enriched"] is True


def _stub_config(tmp_path) -> str:
    """Defined for completeness; D3 wires the real injection seams.

    Until D3 this is never reached (importorskip skips first).
    """
    raise NotImplementedError("Task D3 supplies the stub config + injection seams")
