import hashlib
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from fxlab.models import Observation, PredictionWindow, as_of
from fxlab.providers import ECBReferenceProvider, normalize_ecb
from fxlab.store import save_raw
from fxlab.web import app


def observation(**changes):
    values = dict(observation_id="initial", indicator="GDP", observation_period="2024-Q1",
                  available_at="2024-04-25T12:30:00Z", published_at="2024-04-25T12:30:00Z",
                  ingested_at="2026-09-23T20:00:00Z", time_quality="exact_timestamp", source="fixture",
                  source_url="https://example.test/release", raw_payload_hash="a" * 64,
                  vintage_id="first", actual=1.6, unit="percent")
    values.update(changes)
    return Observation(**values)


def test_revisions_use_historical_availability_not_ingestion_time():
    first = observation()
    revised = observation(observation_id="revision", available_at="2024-05-30T12:30:00Z",
                          published_at="2024-05-30T12:30:00Z", revision_number=1, actual=1.3, vintage_id="second")
    before = datetime(2024, 4, 25, 12, 29, tzinfo=timezone.utc)
    between = datetime(2024, 5, 1, tzinfo=timezone.utc)
    after = datetime(2024, 6, 1, tzinfo=timezone.utc)
    assert as_of([first, revised], before) == []
    assert as_of([first, revised], between)[0].actual == 1.6
    assert as_of([first, revised], after)[0].actual == 1.3


def test_unknown_and_conservative_availability():
    point = datetime(2026, 1, 1, tzinfo=timezone.utc)
    unknown = observation(time_quality="unknown", available_at=None, published_at=None)
    conservative = observation(time_quality="inferred_conservative")
    assert as_of([unknown, conservative], point) == []
    assert as_of([unknown, conservative], point, strict=False) == [conservative]


def test_consensus_and_naive_times_rejected():
    with pytest.raises(ValidationError):
        observation(ingested_at="2026-09-23T20:00:00")
    with pytest.raises(ValidationError):
        observation(surprise=1)
    with pytest.raises(ValidationError):
        observation(consensus=1)
    with pytest.raises(ValueError):
        as_of([observation()], datetime(2026, 1, 1))


def test_forecast_vintage_no_lookahead():
    record = observation(consensus=2, forecast_vintage="2024-05-01T00:00:00Z")
    assert as_of([record], datetime(2024, 4, 26, tzinfo=timezone.utc)) == []


def test_reaction_overlap_rejected():
    with pytest.raises(ValidationError):
        PredictionWindow(mode="reaction_confirmed", prediction_time="2024-01-01T12:00:00Z",
                         target_start="2024-01-01T12:00:00Z", reaction_end="2024-01-01T13:00:00Z")
    with pytest.raises(ValidationError):
        PredictionWindow(mode="post_release", prediction_time="2024-01-01T13:00:00Z", target_start="2024-01-01T12:00:00Z")


XML = b'<Envelope><Cube><Cube time="2024-01-02"><Cube currency="USD" rate="1.1"/></Cube><Cube time="2024-01-03"><Cube currency="USD" rate="1.2"/></Cube></Cube></Envelope>'


def test_offline_replay_and_content_idempotency(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    meta = save_raw("ecb", "https://example.test/fx", XML, {})
    again = save_raw("ecb", "https://example.test/fx", XML, {})
    assert meta["sha256"] == again["sha256"] == hashlib.sha256(XML).hexdigest()
    assert len(list((tmp_path / "bronze/ecb").glob("*.payload"))) == 1
    assert len(list((tmp_path / "bronze/ecb").glob("*.json"))) == 2
    provider = ECBReferenceProvider()
    a = provider.replay(XML, meta, date(2024, 1, 1), date(2024, 1, 4))
    b = provider.replay(XML, again, date(2024, 1, 1), date(2024, 1, 4))
    assert a["normalized_sha256"] == b["normalized_sha256"]
    assert a["strict_pit_eligible"] is False
    with pytest.raises(ValueError):
        provider.replay(XML + b" ", meta, date(2024, 1, 1), date(2024, 1, 4))


def test_normalizer_rejects_duplicates():
    invalid = XML.replace(b"2024-01-03", b"2024-01-02")
    with pytest.raises(ValueError):
        normalize_ecb(invalid, date(2024, 1, 1), date(2024, 1, 4))


def test_web_empty_and_loaded_data(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/market").json()["points"] == []
    assert client.get("/reports/nonexistent").status_code == 404
    assert client.get("/reports/macro-data").status_code == 200
    assert client.get("/reports/positioning").status_code == 200
    assert client.get("/api/positioning").json()["points"] == []
    assert client.get("/reports/policy-events").status_code == 200
    assert client.get("/api/policy").json()["points"] == []
    assert client.get("/api/policy?source=ecb").json()["points"] == []
    assert client.get("/reports/event-alignment").status_code == 200
    assert client.get("/reports/baseline-experiments").status_code == 200
    assert client.get("/reports/causal-graph").status_code == 200
    assert client.get("/api/causal-graph").json()["nodes"] == []
    assert client.get("/download/nonexistent").status_code == 404
    meta = save_raw("ecb", "https://example.test/fx", XML, {})
    ECBReferenceProvider().replay(XML, meta, date(2024, 1, 1), date(2024, 1, 4))
    assert len(client.get("/api/market").json()["points"]) == 2
    assert client.get("/api/market?limit=-1").status_code == 422
    assert client.get("/download/market.json").status_code == 200
