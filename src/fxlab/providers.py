import hashlib
import json
import math
from datetime import date
from typing import Protocol
from xml.etree import ElementTree

import httpx
import duckdb

from .store import atomic_json, root, save_raw

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"


class MarketProvider(Protocol):
    source_name: str

    def backfill(self, start: date, end: date) -> dict: ...


def normalize_ecb(body: bytes, start: date, end: date) -> list[dict]:
    if end < start:
        raise ValueError("end precedes start")
    rows = []
    for cube in ElementTree.fromstring(body).iter():
        if "time" not in cube.attrib:
            continue
        day = date.fromisoformat(cube.attrib["time"])
        if not start <= day <= end:
            continue
        for rate in cube:
            if rate.attrib.get("currency") == "USD":
                rows.append({"date": day, "value": float(rate.attrib["rate"]), "provider": "ecb",
                             "instrument": "EURUSD", "series_type": "reference_rate",
                             "unit": "USD_per_EUR", "time_quality": "unknown",
                             "available_at": None, "historical_vintage_verified": False})
    if not rows:
        raise ValueError("ECB response contains no USD observations in requested interval")
    rows.sort(key=lambda row: row["date"])
    if len({row["date"] for row in rows}) != len(rows):
        raise ValueError("Duplicate ECB dates")
    if any(not math.isfinite(row["value"]) or row["value"] <= 0 for row in rows):
        raise ValueError("Invalid exchange rate")
    return rows


class ECBReferenceProvider:
    source_name = "ecb"

    def backfill(self, start: date, end: date) -> dict:
        transport = httpx.HTTPTransport(retries=2)
        with httpx.Client(transport=transport, timeout=60, follow_redirects=True) as client:
            response = client.get(ECB_URL)
            meta = save_raw(self.source_name, str(response.url), response.content, dict(response.headers), response.status_code)
            response.raise_for_status()
        return self.replay(response.content, meta, start, end)

    def replay(self, body: bytes, meta: dict, start: date, end: date) -> dict:
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError("Raw hash mismatch")
        rows = normalize_ecb(body, start, end)
        for row in rows:
            row["raw_payload_hash"] = meta["sha256"]
        folder = root() / "silver"
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "ecb_eurusd.parquet.tmp"
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE rates (date DATE, value DOUBLE, provider VARCHAR, instrument VARCHAR, series_type VARCHAR, unit VARCHAR, time_quality VARCHAR, available_at TIMESTAMPTZ, historical_vintage_verified BOOLEAN, raw_payload_hash VARCHAR)")
            connection.executemany("INSERT INTO rates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [list(row.values()) for row in rows])
            connection.execute("COPY (SELECT * FROM rates ORDER BY date) TO ? (FORMAT PARQUET)", [str(tmp)])
        tmp.replace(folder / "ecb_eurusd.parquet")
        content = [{**row, "date": str(row["date"])} for row in rows]
        normalized_hash = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        report = {"provider": "ecb", "series_type": "reference_rate", "rows": len(rows),
                  "requested_start": str(start), "requested_end": str(end),
                  "first_date": str(rows[0]["date"]), "last_date": str(rows[-1]["date"]),
                  "raw_sha256": meta["sha256"], "normalized_sha256": normalized_hash,
                  "ingested_at": meta["ingested_at"], "source_url": meta["source_url"],
                  "strict_pit_eligible": False, "ohlc": False,
                  "limitations": ["Reference rate is not an executable price or OHLC bar.",
                                   "Current historical snapshot; historical vintages and exact publication timestamps are unverified.",
                                   "No H1 coverage. No forward trading targets generated from this reference series."]}
        atomic_json(root() / "reports" / "market.json", report)
        return report
