import lzma
import struct
from datetime import date, datetime, timezone

import pytest

from fxlab.acquisition import aggregate_minutes, decode_tick_file


def payload(records):
    return lzma.compress(b"".join(struct.pack(">3I2f", *record) for record in records))


def test_decode_and_aggregate_tick_sample():
    body = payload([
        (1000, 110005, 110000, 2.0, 1.0),
        (59000, 110010, 110002, 3.0, 1.5),
        (61000, 110020, 110015, 1.0, 2.0),
    ])
    ticks = decode_tick_file(body, date(2024, 1, 11))
    meta = {"source_url": "https://example.test/ticks", "sha256": "a" * 64,
            "ingested_at": "2026-09-24T12:00:00+00:00"}
    rows = aggregate_minutes(ticks, meta)
    assert len(rows) == 2
    assert rows[0]["bar_start"] == datetime(2024, 1, 11, tzinfo=timezone.utc)
    assert rows[0]["bid_open"] == 1.1
    assert rows[0]["bid_high"] == 1.10002
    assert rows[0]["bid_close"] == 1.10002
    assert rows[0]["tick_count"] == 2
    assert rows[1]["available_at"] > rows[1]["bar_end"]
    assert rows[1]["historical_vintage_verified"] is False


@pytest.mark.parametrize("records", [
    [(2000, 110005, 110000, 1.0, 1.0), (1000, 110005, 110000, 1.0, 1.0)],
    [(1000, 109999, 110000, 1.0, 1.0)],
    [(86_400_000, 110005, 110000, 1.0, 1.0)],
])
def test_decode_rejects_invalid_ticks(records):
    with pytest.raises(ValueError):
        decode_tick_file(payload(records), date(2024, 1, 11))


def test_decode_rejects_corrupt_payload():
    with pytest.raises(ValueError):
        decode_tick_file(b"not-lzma", date(2024, 1, 11))
