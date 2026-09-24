from datetime import date, datetime, timedelta, timezone

import pytest

from fxlab.market import decode_candles, daily_bars, expected_hours, session_bounds, session_day, validate_schedule, write_parquet

UTC = timezone.utc
META = {"source_url": "https://example.test/h1", "sha256": "a"*64, "ingested_at": "2026-09-23T00:00:00Z"}


def schedule(holidays=None):
    return {"code": "EUR-USD", "defaultTimezone": "America/New_York", "holidays": holidays or [],
            "tradeSchedule": [{"to": None, "sessions": {day: [{"start": "17:00:00", "previousDayStart": True,
                "end": "17:00:00", "previousDayEnd": False}] for day in ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]}}]}


def sample(**kwargs):
    data = {"timestamp": 1704067200000, "multiplier": 0.00001, "open": 1.10000, "high": 1.10100,
            "low": 1.09900, "close": 1.10050, "shift": 3600000,
            "times": [22, 1, 3], "opens": [0, 50, 50], "highs": [0, 20, 10],
            "lows": [0, 30, 10], "closes": [0, 30, 10], "volumes": [10, 20, 30]}
    data.update(kwargs)
    return data


def test_delta_decoder_no_filler_and_no_partial_candle():
    start, end = datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 2, 2, 30, tzinfo=UTC)
    rows, rejected = decode_candles(sample(), META, start, end)
    assert not rejected
    assert [row["bar_start"].hour for row in rows] == [22, 23]
    assert rows[1]["open"] == 1.1005
    assert rows[1]["close"] == 1.1008
    assert rows[0]["available_at"] == datetime(2024, 1, 1, 23, 1, tzinfo=UTC)
    rows, _ = decode_candles(sample(), META, start, end + timedelta(hours=1))
    assert [row["bar_start"].hour for row in rows] == [22, 23, 2]
    assert len(rows) == 3  # no artificial midnight/01:00 rows


def test_invalid_ohlc_quarantine_does_not_break_delta_chain():
    rows, rejected = decode_candles(sample(highs=[-200, 220, 10]), META,
                                   datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC))
    assert len(rejected) == 1 and rejected[0]["reason"] == "invalid_ohlc"
    assert rows[0]["high"] == 1.1012


@pytest.mark.parametrize("change", [{"times": [22, -1, 3]}, {"volumes": [1]}, {"shift": 60000}, {"opens": [0, 0.5, 0]}, {"timestamp": 1704067200001}])
def test_malformed_contract_fails(change):
    with pytest.raises(ValueError):
        decode_candles(sample(**change), META, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC))


def test_session_boundaries_dst_and_weekend():
    assert session_bounds(date(2024, 3, 8))[1].hour == 22
    assert session_bounds(date(2024, 3, 11))[0].hour == 21
    assert session_bounds(date(2024, 11, 4))[0].hour == 22
    assert session_day(datetime(2024, 3, 10, 21, tzinfo=UTC)) == date(2024, 3, 11)
    assert expected_hours(date(2024, 3, 10), []) == []


def test_holidays_subtract_closed_hours():
    start, end = session_bounds(date(2024, 1, 2))
    holidays = [{"from": int(start.timestamp()*1000), "till": int((start+timedelta(hours=2)).timestamp()*1000)}]
    assert len(expected_hours(date(2024, 1, 2), holidays)) == 22
    assert expected_hours(date(2024, 1, 2), holidays)[0] == start + timedelta(hours=2)


def test_daily_missing_hour_not_silently_compressed(tmp_path):
    start, end = session_bounds(date(2024, 1, 2))
    rows = []
    for hour in range(24):
        if hour == 5:
            continue
        rows.append({"bar_start": start + timedelta(hours=hour), "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.11,
                     "volume": 1, "raw_payload_hash": "a"*64})
    daily, missing, outside = daily_bars(rows, start, end, schedule())
    assert len(daily) == 1 and daily[0]["complete"] is False
    assert daily[0]["hours_present"] == 23 and daily[0]["hours_expected"] == 24
    assert len(missing) == 1 and not outside
    write_parquet(daily, tmp_path / "daily.parquet")
    import duckdb
    with duckdb.connect() as con:
        result = con.execute("select complete, bar_end from read_parquet(?)", [str(tmp_path / "daily.parquet")]).fetchone()
    assert result[0] is False and result[1] == end


def test_schedule_change_fails_closed():
    changed = schedule()
    changed["defaultTimezone"] = "UTC"
    with pytest.raises(ValueError):
        validate_schedule(changed)


def test_real_month_fixture():
    import json
    from pathlib import Path
    data = json.loads((Path(__file__).parent / "fixtures/dukascopy_h1_2024_01.json").read_text())
    rows, rejected = decode_candles(data, META, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC))
    assert not rejected
    assert rows[0]["bar_start"] == datetime(2024, 1, 1, 22, tzinfo=UTC)
    assert rows[0]["open"] == 1.10427
    assert rows[0]["close"] == 1.10438
    assert len(rows) == len(data["times"])


def test_pinned_snapshot_ignores_new_registry_and_checks_hash(tmp_path, monkeypatch):
    import hashlib
    import json
    from fxlab.market import fetch_snapshot
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    body = b'{"version": 1}'
    (tmp_path / "original.payload").write_bytes(body)
    meta = {"payload": "original.payload", "sha256": hashlib.sha256(body).hexdigest()}
    url = "https://example.test/month"
    registry = tmp_path / "registry/dukascopy"
    registry.mkdir(parents=True)
    (registry / (hashlib.sha256(url.encode()).hexdigest()+".json")).write_text(json.dumps({"payload": "wrong"}))
    class NoNetwork:
        def get(self, *args, **kwargs):
            raise AssertionError("Replay attempted network access")
    value, _ = fetch_snapshot(NoNetwork(), url, offline=True, pinned={url: meta})
    assert value == {"version": 1}
    (tmp_path / "original.payload").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        fetch_snapshot(NoNetwork(), url, offline=True, pinned={url: meta})


def test_bad_replay_checksum_keeps_current_dataset(tmp_path, monkeypatch):
    import json
    import fxlab.market as market
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    reports = tmp_path / "reports"
    reports.mkdir()
    current = reports / "bars.json"
    current.write_text('{"dataset_id":"existing"}')
    def fake_fetch(client, url, **kwargs):
        if "/instruments/" in url:
            return schedule(), META
        return sample(), META
    monkeypatch.setattr(market, "fetch_snapshot", fake_fetch)
    with pytest.raises(ValueError, match="checksum"):
        market.backfill_bars(date(2024, 1, 1), date(2024, 1, 2), offline=True,
                             cutoff=datetime(2024, 2, 1, tzinfo=UTC), expected_checksum="wrong")
    assert json.loads(current.read_text()) == {"dataset_id": "existing"}
    assert not (tmp_path / "silver").exists()
