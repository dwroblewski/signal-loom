from core import enrich


def test_api_enricher_returns_raw_and_logs_cache_tokens(monkeypatch):
    class R:
        content = [type("B", (), {"text": "```yaml\nenriched: true\n```"})()]
        usage = type("U", (), {"input_tokens": 5, "cache_read_input_tokens": 4000,
                               "cache_creation_input_tokens": 0, "output_tokens": 900})()
    logged = {}
    monkeypatch.setattr(enrich, "_client_create", lambda **k: R())
    monkeypatch.setattr(enrich.telemetry, "log_usage", lambda **k: logged.update(k))
    raw, usage = enrich.ApiEnricher(model="claude-sonnet-4-6").enrich("body", vocabulary={"ai"})
    assert "enriched: true" in raw and usage["cache_read_input_tokens"] == 4000
    assert logged["cache_read_input_tokens"] == 4000   # cost still measured in the extracted tool
