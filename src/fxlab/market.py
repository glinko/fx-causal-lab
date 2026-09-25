"""Direct EUR/USD candles, no synthetic gap filling and no invented vintages."""
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo
from uuid import uuid4

import duckdb
import httpx

from .store import atomic_json, root, save_raw

API = "https://jetta.dukascopy.com/v1"
UTC = timezone.utc
NY = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)
PARSER = "dukascopy-json-1"


def stamp(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, UTC)


def decode_candles(data: dict, meta: dict, start: datetime, end: datetime):
    """Delta encoding validated against dukascopy-node source. Never fill deltas."""
    keys = ("opens", "highs", "lows", "closes", "volumes")
    times = data.get("times")
    if not isinstance(times, list) or any(not isinstance(data.get(k), list) or len(data[k]) != len(times) for k in keys):
        raise ValueError("Candle column lengths differ")
    if data.get("shift") != 3600000:
        raise ValueError("Expected H1 shift in milliseconds")
    if not times:
        return [], []
    multiplier = Decimal(str(data["multiplier"]))
    if not multiplier.is_finite() or multiplier <= 0:
        raise ValueError("Invalid price multiplier")
    names = ("open", "high", "low", "close")
    units = []
    for key in names:
        base = Decimal(str(data[key]))
        if not base.is_finite():
            raise ValueError("Non-finite base candle")
        units.append(int((base / multiplier).to_integral_value(rounding=ROUND_HALF_UP)))
    current = data["timestamp"]
    if not isinstance(current, int) or isinstance(current, bool):
        raise ValueError("Invalid base timestamp")
    rows, rejected = [], []
    for index, delta in enumerate(times):
        if not isinstance(delta, int) or isinstance(delta, bool) or delta < 0:
            raise ValueError("Invalid time delta")
        current += delta * data["shift"]
        for number, key in enumerate(keys[:4]):
            change = data[key][index]
            if not isinstance(change, int) or isinstance(change, bool):
                raise ValueError("Price deltas must be integer units")
            units[number] += change
        timestamp = stamp(current)
        if current % 3600000:
            raise ValueError("H1 timestamp is not aligned to an UTC hour")
        # Include only closed bars entirely inside the requested range.
        if timestamp < start or timestamp + HOUR > end:
            continue
        values = [float(Decimal(unit) * multiplier) for unit in units]
        row = dict(zip(names, values))
        row.update(bar_start=timestamp, bar_end=timestamp + HOUR, volume=data["volumes"][index],
                   provider="dukascopy", instrument="EURUSD", price_side="bid", frequency="H1",
                   available_at=timestamp + HOUR + timedelta(seconds=60),
                   time_quality="inferred_conservative", historical_vintage_verified=False,
                   source_url=meta["source_url"], raw_payload_hash=meta["sha256"], ingested_at=meta["ingested_at"])
        reasons = []
        if any(not math.isfinite(value) or value <= 0 for value in values):
            reasons.append("invalid_price")
        if not row["low"] <= min(row["open"], row["close"]) <= max(row["open"], row["close"]) <= row["high"]:
            reasons.append("invalid_ohlc")
        if not isinstance(row["volume"], (float, int)) or not math.isfinite(row["volume"]) or row["volume"] < 0:
            reasons.append("invalid_volume")
        if reasons:
            rejected.append({"bar_start": timestamp.isoformat(), "reason": ",".join(reasons), "raw_payload_hash": meta["sha256"],
                             **{key: row[key] for key in names}})
        else:
            rows.append(row)
    return rows, rejected


def session_bounds(day: date):
    end = datetime(day.year, day.month, day.day, 17, tzinfo=NY)
    return (end - timedelta(days=1)).astimezone(UTC), end.astimezone(UTC)


def session_day(timestamp: datetime):
    local = timestamp.astimezone(NY)
    return local.date() + timedelta(days=1) if local.hour >= 17 else local.date()


def expected_hours(day: date, holidays: list[dict]):
    """Weekday NY17 sessions, minus provider-declared holiday intervals."""
    if day.weekday() > 4:
        return []
    start, end = session_bounds(day)
    result = []
    current = start
    while current < end:
        segments = [(current, current + HOUR)]
        for holiday in holidays:
            left, right = stamp(holiday["from"]), stamp(holiday["till"])
            split = []
            for a, b in segments:
                if right <= a or left >= b:
                    split.append((a, b))
                else:
                    if a < left:
                        split.append((a, left))
                    if right < b:
                        split.append((right, b))
            segments = split
        if segments:
            result.append(current)
        current += HOUR
    return result


def validate_schedule(instrument):
    if instrument.get("code") != "EUR-USD" or instrument.get("defaultTimezone") != "America/New_York":
        raise ValueError("Unexpected instrument or timezone")
    schedules = instrument.get("tradeSchedule", [])
    if len(schedules) != 1:
        raise ValueError("Multiple historical schedules need explicit implementation")
    expected = {name: [{"start": "17:00:00", "previousDayStart": True, "end": "17:00:00", "previousDayEnd": False}]
                for name in ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY")}
    if schedules[0].get("sessions") != expected or schedules[0].get("to") is not None:
        raise ValueError("Provider trading schedule changed; review calendar")


def daily_bars(hourly, start, end, instrument):
    validate_schedule(instrument)
    groups = defaultdict(dict)
    for row in hourly:
        groups[session_day(row["bar_start"])][row["bar_start"]] = row
    daily, missing, outside = [], [], []
    day = (start - timedelta(days=1)).date()
    while day <= (end + timedelta(days=1)).date():
        left, right = session_bounds(day)
        expected = expected_hours(day, instrument.get("holidays", []))
        in_window = [ts for ts in expected if ts >= start and ts + HOUR <= end]
        found = groups.get(day, {})
        for ts in in_window:
            if ts not in found:
                missing.append({"bar_start": ts.isoformat(), "session_date": str(day), "reason": "missing_expected_hour"})
        for ts, bar in found.items():
            if ts not in expected:
                outside.append({"bar_start": ts.isoformat(), "reason": "outside_provider_schedule", "raw_payload_hash": bar["raw_payload_hash"]})
        bars = [found[ts] for ts in expected if ts in found]
        if bars and right <= end and left >= start:
            daily.append({"session_date": str(day), "bar_start": left, "bar_end": right,
                          "open": bars[0]["open"], "high": max(b["high"] for b in bars),
                          "low": min(b["low"] for b in bars), "close": bars[-1]["close"],
                          "volume": sum(b["volume"] for b in bars), "hours_present": len(bars),
                          "hours_expected": len(expected), "complete": len(bars) == len(expected),
                          "provider": "dukascopy", "instrument": "EURUSD", "price_side": "bid", "frequency": "D1_NY17",
                          "available_at": right + timedelta(seconds=60), "time_quality": "inferred_conservative",
                          "historical_vintage_verified": False,
                          "raw_payload_hashes": sorted({b["raw_payload_hash"] for b in bars})})
        day += timedelta(days=1)
    return daily, missing, outside


def fetch_snapshot(client, url, *, offline=False, refresh=False, pinned=None):
    key = hashlib.sha256(url.encode()).hexdigest()
    cached = root() / "registry" / "dukascopy" / f"{key}.json"
    if pinned is not None or (cached.exists() and (offline or not refresh)):
        if pinned is not None and url not in pinned:
            raise ValueError("Manifest has no snapshot for " + url)
        meta = pinned[url] if pinned is not None else json.loads(cached.read_text(encoding="utf-8"))
        body = (root() / meta["payload"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError("Cached raw checksum mismatch")
        return json.loads(body), meta
    if offline:
        raise ValueError(f"No preserved snapshot for {url}")
    for attempt in range(3):
        response = client.get(url)
        meta = save_raw("dukascopy", str(response.url), response.content, dict(response.headers), response.status_code)
        if response.status_code == 429:
            if attempt < 2:
                try:
                    delay = min(60.0, float(response.headers.get("retry-after", "30")))
                except ValueError:
                    delay = 30.0
                time.sleep(delay)
                continue
            raise RuntimeError("Provider rate limit: stopped, preserve cache and retry later; Retry-After=" + response.headers.get("retry-after", "unspecified"))
        if response.status_code >= 500 and attempt < 2:
            time.sleep(2 ** attempt)
            continue
        response.raise_for_status()
        data = response.json()
        atomic_json(cached, meta)
        # Public historical transport is deliberately throttled; cached months return immediately.
        time.sleep(2.1)
        return data, meta
    raise RuntimeError("Provider unavailable")


def write_parquet(rows, path):
    """JSON import preserves lists and timestamps; SQL fixes temporal types explicitly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.json.tmp")
    parquet_tmp = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    temporary.write_text("\n".join(json.dumps(row, default=str, allow_nan=False) for row in rows), encoding="utf-8")
    try:
        with duckdb.connect() as con:
            con.execute("SET TimeZone='UTC'")
            con.execute("CREATE TABLE rows AS SELECT * REPLACE (bar_start::TIMESTAMPTZ AS bar_start, bar_end::TIMESTAMPTZ AS bar_end, available_at::TIMESTAMPTZ AS available_at) FROM read_json_auto(?)", [str(temporary)])
            con.execute("COPY (SELECT * FROM rows ORDER BY bar_start) TO ? (FORMAT PARQUET)", [str(parquet_tmp)])
        parquet_tmp.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
        parquet_tmp.unlink(missing_ok=True)


def backfill_bars(start: date, end: date, *, offline=False, cutoff=None, pinned=None, expected_checksum=None):
    if end < start:
        raise ValueError("end precedes start")
    now = cutoff or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Cutoff must be timezone aware")
    begin = datetime.combine(start, datetime.min.time(), UTC)
    finish = min(datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC), now)
    if begin >= finish:
        raise ValueError("No closed historical interval")
    all_rows, rejected, snapshots = [], [], []
    with httpx.Client(timeout=40, follow_redirects=True, transport=httpx.HTTPTransport(retries=2)) as client:
        instrument, instrument_meta = fetch_snapshot(client, API + "/instruments/EUR-USD", offline=offline, refresh=True, pinned=pinned)
        validate_schedule(instrument)
        snapshots.append(instrument_meta)
        month = begin.replace(day=1)
        while month < finish:
            following = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
            active = following > now
            url = API + "/candles/hour/EUR-USD/BID/"
            url += f"{month.year}/{month.month}" if not active else f"?from={int(month.timestamp()*1000)}"
            # Active endpoint has no trailing slash before query.
            url = url.replace("BID/?", "BID?")
            data, meta = fetch_snapshot(client, url, offline=offline, refresh=active, pinned=pinned)
            rows, bad = decode_candles(data, meta, begin, finish)
            all_rows.extend(rows)
            rejected.extend(bad)
            snapshots.append(meta)
            print(f"{month:%Y-%m}: {len(rows)} bars, {len(bad)} rejected", flush=True)
            month = following
    duplicates = {ts for ts, count in Counter(row["bar_start"] for row in all_rows).items() if count > 1}
    rejected.extend({"bar_start": row["bar_start"].isoformat(), "reason": "duplicate_timestamp", "raw_payload_hash": row["raw_payload_hash"]}
                    for row in all_rows if row["bar_start"] in duplicates)
    hourly = sorted([row for row in all_rows if row["bar_start"] not in duplicates], key=lambda row: row["bar_start"])
    if not hourly:
        raise ValueError("No usable H1 bars")
    daily, missing, outside = daily_bars(hourly, begin, finish, instrument)
    if not daily:
        raise ValueError("No complete daily windows to aggregate")
    outside_times = {row["bar_start"] for row in outside}
    for row in hourly:
        row["within_schedule"] = row["bar_start"].isoformat() not in outside_times
    normalized = [{k: v for k, v in row.items() if k not in {"ingested_at"}} for row in hourly]
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    if expected_checksum and normalized_sha256 != expected_checksum:
        raise ValueError("Replay differs from recorded normalized checksum; previous dataset retained")
    signature = {"parser": PARSER, "start": begin.isoformat(), "end": finish.isoformat(),
                 "snapshots": [(m["source_url"], m["sha256"]) for m in snapshots]}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root() / "silver" / "dukascopy" / dataset_id
    # Publish only after the entire run has validated, leaving previous current dataset intact on failure.
    if not (folder / "h1.parquet").exists():
        write_parquet(hourly, folder / "h1.parquet")
    if not (folder / "d1.parquet").exists():
        write_parquet(daily, folder / "d1.parquet")
    atomic_json(folder / "gaps.json", missing)
    atomic_json(folder / "quarantine.json", rejected + outside)
    report = {"dataset_id": dataset_id, "parser": PARSER, "provider": "dukascopy", "price_side": "bid",
              "requested_start": str(start), "requested_end": str(end), "cutoff": now.isoformat(),
              "effective_end": finish.isoformat(), "generated_at": datetime.now(UTC).isoformat(),
              "first_bar": hourly[0]["bar_start"].isoformat(), "last_bar_end": hourly[-1]["bar_end"].isoformat(),
              "h1_rows": len(hourly), "d1_rows": len(daily), "complete_d1_rows": sum(row["complete"] for row in daily),
              "missing_expected_hours": len(missing), "rejected_rows": len(rejected), "outside_schedule": len(outside),
              "zero_volume_bars": sum(row["volume"] == 0 for row in hourly),
              "normalized_sha256": normalized_sha256,
              "files": {key: str((folder / filename).relative_to(root())) for key, filename in
                        [("h1", "h1.parquet"), ("d1", "d1.parquet"), ("gaps", "gaps.json"), ("quarantine", "quarantine.json")]},
              "snapshots": snapshots, "calendar": "NY 17:00 with current provider holiday metadata; UTC storage",
              "strict_pit_eligible": False,
              "limitations": ["Current provider history; revisions and exact historical receipt times unverified.",
                              "available_at = bar_end + 60s is an explicit conservative assumption, not observed latency.",
                              "Bid only; no ask, spread or executable strategy PnL.",
                              "Missing hours are not filled. Incomplete daily bars retain complete=false.",
                              "D1 is aggregated from H1 using provider NY17 schedule, not fetched as independent daily data."]}
    atomic_json(folder / "manifest.json", report)
    atomic_json(root() / "reports" / "bars.json", report)
    return report


def replay_bars(manifest: Path):
    previous = json.loads(manifest.read_text(encoding="utf-8"))
    if previous["parser"] != PARSER:
        raise ValueError("Use the original parser version to replay this manifest")
    result = backfill_bars(date.fromisoformat(previous["requested_start"]), date.fromisoformat(previous["requested_end"]),
                          offline=True, cutoff=datetime.fromisoformat(previous["cutoff"]),
                          pinned={m["source_url"]: m for m in previous["snapshots"]}, expected_checksum=previous["normalized_sha256"])
    if result["normalized_sha256"] != previous["normalized_sha256"]:
        raise ValueError("Replay differs from recorded normalized checksum")
    return result
