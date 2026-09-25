"""Free/public daily series inventory backed by actual normalized datasets."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import duckdb
import httpx
import xlrd

from .store import atomic_json, root, save_raw

UTC = timezone.utc
PARSER = "open-daily-coverage-1"
ECB_API = "https://data-api.ecb.europa.eu/service/data/YC"
TREASURY_API = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"

SERIES_META = {
    "EURUSD": {"title": "EUR/USD", "provider": "Dukascopy", "unit": "USD per EUR", "tier": "A"},
    "EURUSD_REF": {"title": "EUR/USD ECB reference", "provider": "ECB", "unit": "USD per EUR", "tier": "A"},
    "US_2Y": {"title": "US Treasury 2Y", "provider": "US Treasury", "unit": "percent", "tier": "A"},
    "EA_2Y": {"title": "Euro-area AAA spot 2Y", "provider": "ECB", "unit": "percent", "tier": "A"},
    "US_EA_2Y": {"title": "US minus EA 2Y spread", "provider": "derived", "unit": "percentage points", "tier": "A"},
    "US_10Y": {"title": "US Treasury 10Y", "provider": "US Treasury", "unit": "percent", "tier": "A"},
    "EA_10Y": {"title": "Euro-area AAA spot 10Y", "provider": "ECB", "unit": "percent", "tier": "A"},
    "US_EA_10Y": {"title": "US minus EA 10Y spread", "provider": "derived", "unit": "percentage points", "tier": "A"},
    "BRENT": {"title": "Brent spot", "provider": "EIA", "unit": "USD per barrel", "tier": "A"},
    "WTI": {"title": "WTI spot", "provider": "EIA", "unit": "USD per barrel", "tier": "A"},
    "VIX": {"title": "Cboe VIX close", "provider": "Cboe", "unit": "index points", "tier": "A"},
}

REQUIRED_COMMON = ["EURUSD_REF", "US_2Y", "EA_2Y", "US_10Y", "EA_10Y", "BRENT", "WTI", "VIX"]

INVENTORY_CANDIDATES = [
    {"series_id": "US_EQUITY", "title": "US equity index/proxy", "tier": "A", "status": "SOURCE_SELECTION",
     "reason": "A long free source with acceptable index licensing still needs validation."},
    {"series_id": "EU_EQUITY", "title": "European equity index/proxy", "tier": "A", "status": "SOURCE_SELECTION",
     "reason": "ECB daily licensed index history was not found in the public FM dataset."},
    {"series_id": "GOLD", "title": "Gold", "tier": "B", "status": "QUEUED", "reason": "After the first common D1 grid."},
    {"series_id": "EIA_INVENTORIES", "title": "US petroleum inventories", "tier": "C", "status": "QUEUED", "reason": "Weekly energy state."},
    {"series_id": "EIA_PRODUCTION", "title": "US oil production", "tier": "C", "status": "QUEUED", "reason": "Weekly/monthly energy state."},
    {"series_id": "TREASURY_TIC", "title": "Treasury TIC flows", "tier": "C", "status": "QUEUED", "reason": "Monthly flow state."},
]

OPTIONAL_PREMIUM = [
    {"dataset": "Historical macro consensus", "status": "OPTIONAL", "providers": "Econoday / Trading Economics / LSEG / Bloomberg",
     "note": "Schema retained; no vendor evaluation or purchase blocks the open-data MVP."},
    {"dataset": "Options dealer gamma / institutional flows", "status": "OPTIONAL", "providers": "licensed market vendors",
     "note": "Integrate only after public-data baselines demonstrate a specific need."},
]


def _fetch(name: str, url: str, *, offline: bool, refresh: bool) -> tuple[bytes, dict]:
    key = hashlib.sha256(url.encode()).hexdigest()
    registry = root() / "registry" / "open_data" / f"{name}-{key}.json"
    if registry.exists() and (offline or not refresh):
        meta = json.loads(registry.read_text(encoding="utf-8"))
        body = (root() / meta["payload"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError(f"Cached checksum mismatch: {name}")
        return body, meta
    if offline:
        raise ValueError(f"No cached open-data snapshot: {name}")
    headers = {"User-Agent": "FX-Causal-Lab/0.13 open-data-research", "Accept": "*/*"}
    with httpx.Client(timeout=90, follow_redirects=True, transport=httpx.HTTPTransport(retries=2)) as client:
        for attempt in range(4):
            response = client.get(url, headers=headers)
            if (response.status_code == 429 or response.status_code >= 500) and attempt < 3:
                time.sleep(2 ** (attempt + 1))
                continue
            response.raise_for_status()
            meta = save_raw("open_data_" + name, str(response.url), response.content, dict(response.headers), response.status_code)
            registry.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(registry, meta)
            return response.content, meta
    raise RuntimeError(f"Source unavailable: {name}")


def parse_treasury_xml(body: bytes) -> dict[str, list[tuple[date, float]]]:
    namespaces = {
        "a": "http://www.w3.org/2005/Atom",
        "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    }
    result = {"US_2Y": [], "US_10Y": []}
    try:
        document = ET.fromstring(body)
    except ET.ParseError as error:
        raise ValueError("Invalid Treasury XML") from error
    for entry in document.findall("a:entry", namespaces):
        properties = entry.find("a:content/m:properties", namespaces)
        if properties is None:
            continue
        fields = {node.tag.split("}")[-1]: node.text for node in properties}
        day = date.fromisoformat(str(fields["NEW_DATE"])[:10])
        for series_id, key in (("US_2Y", "BC_2YEAR"), ("US_10Y", "BC_10YEAR")):
            value = fields.get(key)
            if value not in (None, ""):
                result[series_id].append((day, float(value)))
    if not all(result.values()):
        raise ValueError("Treasury payload lacks 2Y or 10Y observations")
    return result


def parse_ecb_csv(body: bytes, expected_key: str) -> list[tuple[date, float]]:
    text = body.decode("utf-8-sig")
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("KEY") != expected_key or not row.get("OBS_VALUE"):
            continue
        rows.append((date.fromisoformat(row["TIME_PERIOD"]), float(row["OBS_VALUE"])))
    if len(rows) < 1000:
        raise ValueError(f"ECB series unexpectedly short: {expected_key}")
    return rows


def parse_vix_csv(body: bytes) -> list[tuple[date, float]]:
    rows = []
    for row in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))):
        rows.append((datetime.strptime(row["DATE"], "%m/%d/%Y").date(), float(row["CLOSE"])))
    if len(rows) < 5000:
        raise ValueError("Cboe VIX history unexpectedly short")
    return rows


def parse_eia_xls(body: bytes) -> list[tuple[date, float]]:
    workbook = xlrd.open_workbook(file_contents=body)
    sheet = workbook.sheet_by_name("Data 1")
    if sheet.row_values(2)[:1] != ["Date"]:
        raise ValueError("EIA workbook layout changed")
    rows = []
    for index in range(3, sheet.nrows):
        raw_day, raw_value = sheet.cell_value(index, 0), sheet.cell_value(index, 1)
        if raw_day in (None, "") or raw_value in (None, ""):
            continue
        parsed = xlrd.xldate_as_datetime(raw_day, workbook.datemode).date()
        value = float(raw_value)
        if math.isfinite(value):
            rows.append((parsed, value))
    if len(rows) < 5000:
        raise ValueError("EIA daily history unexpectedly short")
    return rows


def _clip(rows: list[tuple[date, float]], start: date, end: date) -> list[tuple[date, float]]:
    selected = [(day, value) for day, value in rows if start <= day <= end]
    if len({day for day, _ in selected}) != len(selected):
        raise ValueError("Duplicate observation date")
    if any(not math.isfinite(value) for _, value in selected):
        raise ValueError("Non-finite observation")
    return sorted(selected)


def _load_eurusd(start: date, end: date) -> tuple[list[tuple[date, float]], dict]:
    report_path = root() / "reports" / "bars.json"
    if not report_path.exists():
        raise FileNotFoundError("EUR/USD bars report is not available locally")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    path = root() / report["files"]["d1"]
    if not path.exists():
        raise FileNotFoundError(f"EUR/USD D1 Parquet is not available locally: {path}")
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT session_date, close FROM read_parquet(?) WHERE complete ORDER BY session_date", [str(path)]
        ).fetchall()
    return _clip([(date.fromisoformat(str(day)), float(value)) for day, value in rows], start, end), report


def _load_eurusd_reference(start: date, end: date) -> tuple[list[tuple[date, float]], dict]:
    report_path = root() / "reports" / "market.json"
    path = root() / "silver" / "ecb_eurusd.parquet"
    if not report_path.exists() or not path.exists():
        raise ValueError("ECB EUR/USD reference backfill is required")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT date, value FROM read_parquet(?) ORDER BY date", [str(path)]
        ).fetchall()
    return _clip([(date.fromisoformat(str(day)), float(value)) for day, value in rows], start, end), report


def _observation_rows(series_id: str, values: list[tuple[date, float]], source_url: str,
                      raw_hash: str, ingested_at: str) -> list[dict]:
    meta = SERIES_META[series_id]
    return [{
        "observation_date": day,
        "event_time": datetime.combine(day, clock_time.min, UTC),
        "value": value,
        "series_id": series_id,
        "unit": meta["unit"],
        "provider": meta["provider"],
        "frequency": "D1",
        "published_at": None,
        "available_at": None,
        "ingested_at": datetime.fromisoformat(ingested_at.replace("Z", "+00:00")),
        "revision_id": None,
        "quality": "current_history_unknown_vintage",
        "strict_pit_eligible": False,
        "source_url": source_url,
        "raw_payload_hash": raw_hash,
    } for day, value in values]


def _write_series(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = path.with_suffix(f".{uuid4().hex}.json.tmp")
    parquet_tmp = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    json_tmp.write_text("\n".join(json.dumps(row, default=str, allow_nan=False) for row in rows), encoding="utf-8")
    try:
        with duckdb.connect() as connection:
            connection.execute("SET TimeZone='UTC'")
            connection.execute(
                "CREATE TABLE observations AS SELECT observation_date::DATE AS observation_date,"
                "event_time::TIMESTAMPTZ AS event_time,"
                "value::DOUBLE AS value,series_id::VARCHAR AS series_id,unit::VARCHAR AS unit,provider::VARCHAR AS provider,"
                "frequency::VARCHAR AS frequency,published_at::TIMESTAMPTZ AS published_at,"
                "available_at::TIMESTAMPTZ AS available_at,ingested_at::TIMESTAMPTZ AS ingested_at,"
                "revision_id::VARCHAR AS revision_id,quality::VARCHAR AS quality,"
                "strict_pit_eligible::BOOLEAN AS strict_pit_eligible,source_url::VARCHAR AS source_url,"
                "raw_payload_hash::VARCHAR AS raw_payload_hash FROM read_json_auto(?)",
                [str(json_tmp)],
            )
            connection.execute("COPY observations TO ? (FORMAT PARQUET)", [str(parquet_tmp)])
        parquet_tmp.replace(path)
    finally:
        json_tmp.unlink(missing_ok=True)
        parquet_tmp.unlink(missing_ok=True)


def _coverage(series_id: str, values: list[tuple[date, float]], path: Path) -> dict:
    first, last = values[0][0], values[-1][0]
    weekdays = sum(1 for offset in range((last - first).days + 1) if (first + timedelta(days=offset)).weekday() < 5)
    coverage = len(values) / weekdays if weekdays else 0
    target_start = date(2005, 1, 1)
    status = "READY" if first <= target_start and len(values) >= 1000 else "PARTIAL"
    return {**SERIES_META[series_id], "series_id": series_id, "frequency": "D1", "from": str(first), "to": str(last),
            "rows": len(values), "weekday_coverage": coverage, "missing_weekdays": max(0, weekdays - len(values)),
            "status": status, "strict_pit_eligible": False, "file": path.relative_to(root()).as_posix()}


def _write_common(series: dict[str, list[tuple[date, float]]], path: Path) -> dict:
    maps = {name: dict(series[name]) for name in REQUIRED_COMMON}
    common = sorted(set.intersection(*(set(values) for values in maps.values())))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    columns = ",".join(f'"{name}" DOUBLE' for name in REQUIRED_COMMON)
    placeholders = ",".join("?" for _ in range(len(REQUIRED_COMMON) + 1))
    with duckdb.connect() as connection:
        connection.execute(f"CREATE TABLE common(observation_date DATE,{columns})")
        connection.executemany(f"INSERT INTO common VALUES ({placeholders})",
                               [(day, *(maps[name][day] for name in REQUIRED_COMMON)) for day in common])
        connection.execute("COPY common TO ? (FORMAT PARQUET)", [str(temporary)])
    temporary.replace(path)
    return {"from": str(common[0]) if common else None, "to": str(common[-1]) if common else None,
            "rows": len(common), "required_series": REQUIRED_COMMON, "file": path.relative_to(root()).as_posix()}


def build_open_data_coverage(start: date = date(2004, 9, 6), end: date | None = None, *,
                             offline: bool = False, refresh: bool = False) -> dict:
    if offline and refresh:
        raise ValueError("offline and refresh are mutually exclusive")
    end = end or date.today()
    if end < start:
        raise ValueError("end precedes start")
    values: dict[str, list[tuple[date, float]]] = {"US_2Y": [], "US_10Y": []}
    source_meta: dict[str, list[dict]] = {}

    treasury_meta = []
    for year in range(start.year, end.year + 1):
        url = f"{TREASURY_API}?data=daily_treasury_yield_curve&field_tdr_date_value={year}"
        body, meta = _fetch(f"treasury-{year}", url, offline=offline, refresh=refresh)
        parsed = parse_treasury_xml(body)
        values["US_2Y"].extend(parsed["US_2Y"])
        values["US_10Y"].extend(parsed["US_10Y"])
        treasury_meta.append(meta)
    source_meta["US_2Y"] = source_meta["US_10Y"] = treasury_meta

    for series_id, maturity in (("EA_2Y", "SR_2Y"), ("EA_10Y", "SR_10Y")):
        key = f"YC.B.U2.EUR.4F.G_N_A.SV_C_YM.{maturity}"
        url = f"{ECB_API}/B.U2.EUR.4F.G_N_A.SV_C_YM.{maturity}?startPeriod={start}&endPeriod={end}&format=csvdata"
        body, meta = _fetch(series_id.lower(), url, offline=offline, refresh=refresh)
        values[series_id] = parse_ecb_csv(body, key)
        source_meta[series_id] = [meta]

    for series_id, url in (
        ("BRENT", "https://www.eia.gov/dnav/pet/hist_xls/RBRTEd.xls"),
        ("WTI", "https://www.eia.gov/dnav/pet/hist_xls/RWTCd.xls"),
    ):
        body, meta = _fetch(series_id.lower(), url, offline=offline, refresh=refresh)
        values[series_id] = parse_eia_xls(body)
        source_meta[series_id] = [meta]
    vix_url = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
    body, meta = _fetch("vix", vix_url, offline=offline, refresh=refresh)
    values["VIX"] = parse_vix_csv(body)
    source_meta["VIX"] = [meta]
    unavailable_series = []
    try:
        values["EURUSD"], bars_report = _load_eurusd(start, end)
        source_meta["EURUSD"] = bars_report["snapshots"]
    except FileNotFoundError as exc:
        unavailable_series.append({
            "series_id": "EURUSD", "title": SERIES_META["EURUSD"]["title"], "tier": "A",
            "status": "MISSING_LOCAL_DATA", "reason": str(exc),
        })
    values["EURUSD_REF"], reference_report = _load_eurusd_reference(start, end)
    source_meta["EURUSD_REF"] = [{"source_url": reference_report["source_url"],
                                  "sha256": reference_report["raw_sha256"],
                                  "ingested_at": reference_report["ingested_at"]}]

    for series_id in list(values):
        values[series_id] = _clip(values[series_id], start, end)
        if not values[series_id]:
            raise ValueError(f"No observations after clipping: {series_id}")
    for spread, left, right in (("US_EA_2Y", "US_2Y", "EA_2Y"), ("US_EA_10Y", "US_10Y", "EA_10Y")):
        left_map, right_map = dict(values[left]), dict(values[right])
        values[spread] = [(day, left_map[day] - right_map[day]) for day in sorted(set(left_map) & set(right_map))]

    normalized = {name: [(str(day), value) for day, value in rows] for name, rows in values.items()}
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    signature = {"parser": PARSER, "start": str(start), "end": str(end), "sha256": normalized_sha256}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root() / "silver" / "open_daily" / dataset_id
    coverage = []
    now = datetime.now(UTC).isoformat()
    for series_id, observations in values.items():
        path = folder / f"{series_id.lower()}.parquet"
        if SERIES_META[series_id]["provider"] == "derived":
            meta = {"source_url": f"derived:{series_id}", "sha256": normalized_sha256, "ingested_at": now}
        else:
            metas = source_meta[series_id]
            meta = {"source_url": metas[-1]["source_url"], "sha256": hashlib.sha256(
                "".join(item["sha256"] for item in metas).encode()).hexdigest(), "ingested_at": now}
        _write_series(_observation_rows(series_id, observations, meta["source_url"], meta["sha256"], meta["ingested_at"]), path)
        coverage.append(_coverage(series_id, observations, path))
    common = _write_common(values, root() / "gold" / "open_daily" / dataset_id / "common_d1.parquet")

    existing = []
    for filename, series_id, title, frequency in (
        ("cftc.json", "CFTC_EUR", "CFTC EUR positioning", "weekly"),
        ("macro_releases.json", "US_MACRO_EVENTS", "BLS CPI/NFP first releases", "event/monthly"),
    ):
        report_path = root() / "reports" / filename
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            existing.append({"series_id": series_id, "title": title, "frequency": frequency,
                             "from": report.get("first_published_at") or report.get("first_report_date"),
                             "to": report.get("last_published_at") or report.get("last_report_date"),
                             "rows": report.get("observation_rows") or report.get("rows"), "status": "READY_NON_STRICT", "tier": "B"})
    report = {
        "dataset_id": dataset_id, "parser": PARSER, "normalized_sha256": normalized_sha256,
        "generated_at": now, "requested_start": str(start), "requested_end": str(end),
        "integrated_series": sorted(coverage, key=lambda row: (row["tier"], row["series_id"])),
        "existing_event_series": existing, "unavailable_series": unavailable_series,
        "candidates": INVENTORY_CANDIDATES,
        "optional_premium": OPTIONAL_PREMIUM, "common_overlap": common,
        "counts": {"integrated": len(coverage) + len(existing), "continuous_d1": len(coverage),
                   "ready": sum(row["status"] == "READY" for row in coverage),
                   "partial": sum(row["status"] == "PARTIAL" for row in coverage),
                   "missing_optional": len(unavailable_series),
                   "candidates": len(INVENTORY_CANDIDATES), "paid_optional": len(OPTIONAL_PREMIUM)},
        "strict_pit_eligible": False,
        "limitations": [
            "Downloaded history proves local availability and normalization, not historical point-in-time vintages.",
            "No missing weekday is forward-filled in source Parquet or the common inner-join grid.",
            "ECB AAA yield curves are model-estimated euro-area curves, not a directly traded sovereign instrument.",
            "The long common grid uses the ECB EUR/USD daily reference rate; Dukascopy H1/NY17 D1 remains a separate tradable-price series.",
            "Consensus is optional and does not block this dataset or the spectral MVP.",
        ],
        "files": {"common_d1": common["file"], **{row["series_id"]: row["file"] for row in coverage}},
    }
    atomic_json(root() / "reports" / "data_coverage.json", report)
    return report
