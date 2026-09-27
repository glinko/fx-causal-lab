"""Official EIA weekly crude-oil fundamentals with conservative availability."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import xlrd

from .macro import write_macro_parquet
from .store import atomic_json, root, save_raw

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
PARSER = "eia-weekly-petroleum-1"
SOURCES = {
    "stocks": "https://www.eia.gov/dnav/pet/xls/PET_STOC_WSTK_DCU_NUS_W.xls",
    "supply": "https://www.eia.gov/dnav/pet/xls/PET_SUM_SNDW_DCUS_NUS_W.xls",
}
SERIES = {
    "EIA_COMMERCIAL_CRUDE_STOCKS": {
        "snapshot": "stocks", "sheet": "Data 1", "source_key": "WCESTUS1",
        "unit": "thousand_barrels", "title": "U.S. commercial crude stocks excluding SPR",
    },
    "EIA_US_CRUDE_PRODUCTION": {
        "snapshot": "supply", "sheet": "Data 1", "source_key": "WCRFPUS2",
        "unit": "thousand_barrels_per_day", "title": "U.S. field production of crude oil",
    },
}


def _parse_sheet(sheet, source_key: str, date_parser, minimum_rows: int = 500) -> list[tuple[date, float]]:
    keys = sheet.row_values(1)
    if not keys or keys[0] != "Sourcekey" or source_key not in keys:
        raise ValueError(f"EIA source key is missing: {source_key}")
    column = keys.index(source_key)
    if sheet.cell_value(2, 0) != "Date":
        raise ValueError("EIA workbook layout changed")
    rows = []
    for index in range(3, sheet.nrows):
        raw_day, raw_value = sheet.cell_value(index, 0), sheet.cell_value(index, column)
        if raw_day in (None, "") or raw_value in (None, ""):
            continue
        day, value = date_parser(raw_day), float(raw_value)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid EIA value for {source_key}")
        rows.append((day, value))
    rows.sort()
    if len(rows) < minimum_rows:
        raise ValueError(f"EIA series unexpectedly short: {source_key}")
    if len({day for day, _ in rows}) != len(rows):
        raise ValueError(f"Duplicate EIA observation date: {source_key}")
    return rows


def parse_workbook(body: bytes, sheet_name: str, source_key: str, minimum_rows: int = 500) -> list[tuple[date, float]]:
    workbook = xlrd.open_workbook(file_contents=body)
    sheet = workbook.sheet_by_name(sheet_name)
    return _parse_sheet(sheet, source_key,
                        lambda value: xlrd.xldate_as_datetime(value, workbook.datemode).date(), minimum_rows)


def normalize_series(series_id: str, values: list[tuple[date, float]], meta: dict,
                     start: date, end: date) -> list[dict]:
    definition = SERIES[series_id]
    rows = []
    for observation_date, value in values:
        if not start <= observation_date <= end:
            continue
        # The normal Wednesday release and one-day holiday delays both precede this conservative boundary.
        available_at = datetime.combine(observation_date + timedelta(days=7), time(0), NY)
        rows.append({
            "observation_id": f"{series_id.lower()}-{observation_date}",
            "series_id": series_id, "title": definition["title"], "observation_date": str(observation_date),
            "event_time": datetime.combine(observation_date, time(0), UTC), "published_at": None,
            "available_at": available_at, "ingested_at": meta["ingested_at"], "value": value,
            "unit": definition["unit"], "frequency": "weekly", "source": "eia",
            "time_quality": "inferred_conservative", "revision_id": None,
            "strict_pit_eligible": False, "source_url": meta["source_url"],
            "raw_payload_hash": meta["sha256"],
        })
    if not rows:
        raise ValueError(f"No EIA observations in requested range: {series_id}")
    return rows


def fetch_eia_snapshots(start: date, end: date) -> dict:
    if end < start:
        raise ValueError("end precedes start")
    snapshots = {}
    with httpx.Client(timeout=60, follow_redirects=True, transport=httpx.HTTPTransport(retries=2),
                      headers={"User-Agent": "FXCausalLab/0.24 research"}) as client:
        for name, url in SOURCES.items():
            response = client.get(url)
            meta = save_raw(f"eia_weekly_{name}", str(response.url), response.content,
                            dict(response.headers), response.status_code)
            response.raise_for_status()
            snapshots[name] = meta
    manifest = {"provider": "eia_weekly_petroleum", "requested_start": str(start),
                "requested_end": str(end), "snapshots": snapshots,
                "fetched_at": datetime.now(UTC).isoformat()}
    atomic_json(root() / "reports" / "eia_fetch.json", manifest)
    return manifest


def replay_eia(manifest: Path | dict) -> dict:
    source = json.loads(manifest.read_text(encoding="utf-8")) if isinstance(manifest, Path) else manifest
    start, end = date.fromisoformat(source["requested_start"]), date.fromisoformat(source["requested_end"])
    payloads = {}
    for name, meta in source["snapshots"].items():
        body = (root() / meta["payload"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError("EIA raw checksum mismatch")
        payloads[name] = body
    rows, coverage = [], {}
    for series_id, definition in SERIES.items():
        meta = source["snapshots"][definition["snapshot"]]
        values = parse_workbook(payloads[definition["snapshot"]], definition["sheet"], definition["source_key"])
        selected = normalize_series(series_id, values, meta, start, end)
        rows.extend(selected)
        coverage[series_id] = {"title": definition["title"], "from": selected[0]["observation_date"],
                               "to": selected[-1]["observation_date"], "rows": len(selected),
                               "unit": definition["unit"], "status": "READY_NON_STRICT"}
    rows.sort(key=lambda row: (row["observation_date"], row["series_id"]))
    normalized = [{key: value for key, value in row.items() if key != "ingested_at"} for row in rows]
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    signature = {"parser": PARSER, "normalized_sha256": normalized_sha256,
                 "snapshots": [(name, meta["sha256"]) for name, meta in sorted(source["snapshots"].items())]}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root() / "silver" / "eia" / dataset_id
    write_macro_parquet(rows, folder / "weekly_petroleum.parquet",
                        ("event_time", "published_at", "available_at", "ingested_at"))
    report = {
        "dataset_id": dataset_id, "parser": PARSER, "provider": source["provider"],
        "requested_start": source["requested_start"], "requested_end": source["requested_end"],
        "generated_at": datetime.now(UTC).isoformat(), "rows": len(rows), "series": coverage,
        "normalized_sha256": normalized_sha256,
        "files": {"weekly_petroleum": (folder / "weekly_petroleum.parquet").relative_to(root()).as_posix()},
        "snapshots": source["snapshots"], "strict_pit_eligible": False,
        "limitations": [
            "The workbooks are current-history snapshots and do not preserve old revisions.",
            "Historical publication timestamps are not present in the workbook.",
            "available_at is conservatively set to the Friday after the observation week; it is not an observed receipt time.",
            "The series can be used in non-strict daily research only after that availability boundary.",
        ],
    }
    atomic_json(folder / "manifest.json", report)
    atomic_json(root() / "reports" / "eia_energy.json", report)
    return report


def backfill_eia(start: date, end: date) -> dict:
    return replay_eia(fetch_eia_snapshots(start, end))
