"""Bounded provider due diligence for consensus vintages and narrow-window FX."""
from __future__ import annotations

import hashlib
import json
import lzma
import math
import statistics
import struct
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from .store import atomic_json, root, save_raw

UTC = timezone.utc
PARSER = "consensus-fx-acquisition-audit-1"
SAMPLE_DAY = date(2024, 1, 11)
SAMPLE_EVENT = datetime(2024, 1, 11, 13, 30, tzinfo=UTC)
POINT_VALUE = 100_000

SOURCES = {
    "te_pit": "https://docs.tradingeconomics.com/economic_calendar/point-in-time/",
    "te_intraday": "https://docs.tradingeconomics.com/markets/intraday/",
    "te_pricing": "https://tradingeconomics.com/api/pricing.aspx?source=api",
    "econoday": "https://www.econoday.com/enterprise/global-economic-data/",
    "dukascopy_docs": "https://www.dukascopy.com/wiki/en/development/data-export/",
    "dukascopy_tick": "https://datafeed.dukascopy.com/datafeed/EURUSD/2024/00/11_ticks.bi5",
}

CONSENSUS_CANDIDATES = [
    {
        "id": "econoday",
        "provider": "Econoday",
        "status": "sample_and_quote_required",
        "documented": "Historical archives since 2001, consensus, actual-as-released, revisions and event timing history.",
        "access": "Enterprise demo / quote; public fixed price not found.",
        "fit": "Strongest explicit historical-as-released claim in the public material reviewed.",
        "unverified": "API entitlement, exact CPI/NFP coverage, consensus freeze time, export rights and price.",
    },
    {
        "id": "trading_economics",
        "provider": "Trading Economics",
        "status": "trial_validation_required",
        "documented": "PIT economic calendar with Date, Actual, Previous, Forecast, Revised and LastUpdate fields.",
        "access": "Standard API page advertises a paid trial and monthly plan; PIT entitlement and usable depth remain unverified.",
        "fit": "Transparent REST schema and the same vendor also documents one-minute-derived intraday markets.",
        "unverified": "Whether historical Forecast is the last pre-release vintage, CPI/NFP depth, redistribution and research-storage rights.",
    },
    {
        "id": "lseg_reuters_polls",
        "provider": "LSEG / Reuters Polls",
        "status": "defer_enterprise",
        "documented": "Forecast mean/median/count/date/time, prior/revised values and actual event/publish timestamps are documented fields.",
        "access": "Licensed third-party content; public release notes state that it becomes fee-liable.",
        "fit": "Rich schema for forecast vintages and release timing.",
        "unverified": "Current product entitlement, historical CPI/NFP depth, delivery path and price.",
    },
    {
        "id": "bloomberg",
        "provider": "Bloomberg",
        "status": "defer_enterprise",
        "documented": "Consensus, contributor forecasts, historical surprise analysis and programmatic BQL/Data License access.",
        "access": "Terminal or enterprise data product; quote required.",
        "fit": "Broad institutional coverage, but outside the minimal-budget bootstrap.",
        "unverified": "Exact fields, vintage semantics, history, entitlement and price for this project.",
    },
]

ACCEPTANCE_CHECKS = [
    "US CPI and NFP history reaches at least 2016, preferably 2010 or earlier.",
    "Forecast is the final consensus observable before the scheduled release, with a documented freeze or vintage timestamp.",
    "Actual, previous, revised_previous, units, reference period, event time and stable event ID are present.",
    "Corrections and later revisions do not overwrite the pre-event forecast or first-release actual.",
    "At least 95% of target CPI/NFP releases contain a numeric consensus after exclusions are explained.",
    "License permits local reproducible research storage and derived reports for one researcher.",
    "A small sample export can be compared with official BLS releases before purchase.",
]


def decode_tick_file(body: bytes, day: date, *, point_value: int = POINT_VALUE) -> list[dict]:
    try:
        decoded = lzma.decompress(body)
    except lzma.LZMAError as error:
        raise ValueError("Invalid Dukascopy LZMA payload") from error
    if not decoded or len(decoded) % 20:
        raise ValueError("Dukascopy tick payload is empty or misaligned")
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    ticks, previous_ms = [], -1
    for offset in range(0, len(decoded), 20):
        milliseconds, ask_units, bid_units, ask_volume, bid_volume = struct.unpack(">3I2f", decoded[offset:offset + 20])
        ask, bid = ask_units / point_value, bid_units / point_value
        if milliseconds < previous_ms or milliseconds >= 86_400_000:
            raise ValueError("Tick timestamps are unordered or outside the UTC day")
        if not (0 < bid <= ask and all(math.isfinite(value) and value >= 0 for value in (ask_volume, bid_volume))):
            raise ValueError("Invalid tick price or volume")
        ticks.append({"timestamp": midnight + timedelta(milliseconds=milliseconds), "bid": bid, "ask": ask,
                      "bid_volume": bid_volume, "ask_volume": ask_volume})
        previous_ms = milliseconds
    return ticks


def aggregate_minutes(ticks: list[dict], meta: dict) -> list[dict]:
    groups = defaultdict(list)
    for tick in ticks:
        groups[tick["timestamp"].replace(second=0, microsecond=0)].append(tick)
    rows = []
    for minute, values in sorted(groups.items()):
        bids, asks = [item["bid"] for item in values], [item["ask"] for item in values]
        rows.append({
            "bar_start": minute,
            "bar_end": minute + timedelta(minutes=1),
            "bid_open": bids[0], "bid_high": max(bids), "bid_low": min(bids), "bid_close": bids[-1],
            "ask_open": asks[0], "ask_high": max(asks), "ask_low": min(asks), "ask_close": asks[-1],
            "tick_count": len(values), "provider": "dukascopy", "instrument": "EURUSD", "frequency": "M1",
            "available_at": minute + timedelta(minutes=1, seconds=60), "time_quality": "inferred_conservative",
            "historical_vintage_verified": False, "source_url": meta["source_url"],
            "raw_payload_hash": meta["sha256"], "ingested_at": meta["ingested_at"],
        })
    return rows


def _snapshot(name: str, url: str, *, offline: bool) -> tuple[bytes, dict]:
    registry = root()/"registry"/"acquisition"/f"{name}.json"
    if offline:
        if not registry.exists():
            raise ValueError(f"No cached acquisition snapshot: {name}")
        meta = json.loads(registry.read_text(encoding="utf-8"))
        body = (root()/meta["payload"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError(f"Cached acquisition snapshot hash mismatch: {name}")
        return body, meta
    headers = {"User-Agent": "FX-Causal-Lab/0.12 source-audit"}
    with httpx.Client(timeout=45, follow_redirects=True, transport=httpx.HTTPTransport(retries=2)) as client:
        for attempt in range(4):
            response = client.get(url, headers=headers)
            if response.status_code == 429 and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            response.raise_for_status()
            meta = save_raw("acquisition_" + name, str(response.url), response.content, dict(response.headers), response.status_code)
            registry.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(registry, meta)
            return response.content, meta
    raise RuntimeError(f"Provider unavailable: {name}")


def _validate_document(name: str, body: bytes) -> None:
    text = body.decode("utf-8", errors="ignore").lower()
    required = {
        "te_pit": ["point-in-time data", "forecast", "revised", "lastupdate"],
        "te_intraday": ["maximum of 30 days", "1-minute", "10,000"],
        "te_pricing": ["199", "49"],
        "econoday": ["historical archives dating back to 2001", "consensus data", "actual-as-released"],
        "dukascopy_docs": ["day_ticks.bi5", "milliseconds since start of day", "ask price", "bid price"],
    }
    missing = [value for value in required[name] if value not in text]
    if missing:
        raise ValueError(f"Documentation contract changed for {name}: {missing}")


def _write_minutes(rows: list[dict], path: Path) -> None:
    import duckdb
    path.parent.mkdir(parents=True, exist_ok=True)
    source = path.with_suffix(f".{uuid4().hex}.json.tmp")
    target = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    source.write_text("\n".join(json.dumps(row, default=str, allow_nan=False) for row in rows), encoding="utf-8")
    try:
        with duckdb.connect() as connection:
            connection.execute("SET TimeZone='UTC'")
            connection.execute("CREATE TABLE rows AS SELECT * REPLACE (bar_start::TIMESTAMPTZ AS bar_start, "
                               "bar_end::TIMESTAMPTZ AS bar_end, available_at::TIMESTAMPTZ AS available_at, "
                               "ingested_at::TIMESTAMPTZ AS ingested_at) FROM read_json_auto(?)", [str(source)])
            connection.execute("COPY (SELECT * FROM rows ORDER BY bar_start) TO ? (FORMAT PARQUET)", [str(target)])
        target.replace(path)
    finally:
        source.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


def build_acquisition_review(*, offline: bool = False) -> dict:
    snapshots, payloads = {}, {}
    for name, url in SOURCES.items():
        body, meta = _snapshot(name, url, offline=offline)
        payloads[name], snapshots[name] = body, meta
        if name != "dukascopy_tick":
            _validate_document(name, body)
    ticks = decode_tick_file(payloads["dukascopy_tick"], SAMPLE_DAY)
    minutes = aggregate_minutes(ticks, snapshots["dukascopy_tick"])
    if not minutes:
        raise ValueError("No M1 rows decoded")
    spreads = [(tick["ask"] - tick["bid"]) * 10_000 for tick in ticks]
    sorted_spreads = sorted(spreads)
    p95 = sorted_spreads[min(len(sorted_spreads) - 1, int(len(sorted_spreads) * .95))]
    window_start, window_end = SAMPLE_EVENT - timedelta(minutes=30), SAMPLE_EVENT + timedelta(minutes=30)
    window = [row for row in minutes if window_start <= row["bar_start"] <= window_end]
    normalized = {
        "snapshots": {name: meta["sha256"] for name, meta in snapshots.items()},
        "sample_day": str(SAMPLE_DAY),
        "tick_count": len(ticks),
        "minute_rows": len(minutes),
        "first_tick": ticks[0]["timestamp"].isoformat(),
        "last_tick": ticks[-1]["timestamp"].isoformat(),
        "event_window_rows": len(window),
        "median_spread_pips": statistics.median(spreads),
        "p95_spread_pips": p95,
        "candidates": CONSENSUS_CANDIDATES,
        "acceptance_checks": ACCEPTANCE_CHECKS,
    }
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
    dataset_id = hashlib.sha256(json.dumps({"parser": PARSER, "normalized_sha256": normalized_sha256}, sort_keys=True).encode()).hexdigest()[:20]
    output = root()/"silver"/"dukascopy_tick_sample"/dataset_id/"eurusd_m1.parquet"
    _write_minutes(minutes, output)
    report = {
        "dataset_id": dataset_id, "parser": PARSER, "normalized_sha256": normalized_sha256,
        "generated_at": datetime.now(UTC).isoformat(), "offline": offline,
        "decision": {
            "free_fx_pilot": "ready_non_strict",
            "consensus_procurement": "not_ready",
            "next_action": "request_same_sample_from_econoday_and_trading_economics",
            "reason": "Dukascopy tick transport is verified; no consensus vendor has yet passed vintage, coverage, license and price acceptance checks.",
        },
        "consensus_candidates": CONSENSUS_CANDIDATES,
        "acceptance_checks": ACCEPTANCE_CHECKS,
        "fx_sample": {
            "provider": "Dukascopy", "instrument": "EURUSD", "price_sides": ["bid", "ask"],
            "sample_day": str(SAMPLE_DAY), "reference_event": SAMPLE_EVENT.isoformat(),
            "tick_count": len(ticks), "minute_rows": len(minutes), "event_window_m1_rows": len(window),
            "first_tick": ticks[0]["timestamp"].isoformat(), "last_tick": ticks[-1]["timestamp"].isoformat(),
            "median_spread_pips": statistics.median(spreads), "p95_spread_pips": p95,
            "strict_pit_eligible": False,
            "limitation": "The current history endpoint proves retrievability and UTC structure, not an immutable historical market vintage or redistribution right.",
        },
        "documented_constraints": {
            "te_calendar": "Maximum 1,000 calendar rows per request; plan-specific request allowance.",
            "te_intraday": "Maximum 30-day range per request and 10,000 underlying one-minute records; history depth for EUR/USD is not stated.",
            "dukascopy": "One compressed tick file per instrument/day; missing file means no recorded ticks according to provider documentation.",
        },
        "snapshots": {name: {key: meta[key] for key in ("source_url", "sha256", "payload", "status_code")} for name, meta in snapshots.items()},
        "files": {"m1_sample": output.relative_to(root()).as_posix()},
        "limitations": [
            "No paid account or trial was opened.",
            "Vendor claims are not accepted as proof of entitlement or pre-release vintage semantics.",
            "The one-day FX sample validates decoding and event-window coverage only.",
            "No strict experiment is enabled by this audit.",
        ],
    }
    atomic_json(root()/"reports"/"acquisition_review.json", report)
    return report
