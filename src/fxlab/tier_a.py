"""Tier A world-state acquisition and point-in-time feature matrix.

Sparse weekly/monthly observations are joined as-of their modeled availability;
rows before the first release remain NULL. A failed provider blocks only the
experiments that require it.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import io
import json
import math
import zipfile
import xml.etree.ElementTree as ET
from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import duckdb
import httpx

from .store import atomic_json, root, save_raw

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
PARSER = "tier-a-world-state-2"

GSW_URL = "https://www.federalreserve.gov/data/yield-curve-tables/feds200805.csv"
NFCI_URL = "https://api.data.chicagofed.org/NFCI/nfci-chart-series-csv.csv"
H41_URL = "https://www.federalreserve.gov/datadownload/Output.aspx?rel=H41&filetype=zip"
TIC_URL = "https://ticdata.treasury.gov/resource-center/data-chart-center/tic/Documents/slt_table3.txt"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?period1=946684800&period2=4102444800&interval=1d&events=history"

SERIES_META = {
    "BKEVEN05": ("Federal Reserve Board GSW", "percent", "daily_close"),
    "BKEVEN10": ("Federal Reserve Board GSW", "percent", "daily_close"),
    "TIPSY05": ("Federal Reserve Board GSW", "percent", "daily_close"),
    "TIPSY10": ("Federal Reserve Board GSW", "percent", "daily_close"),
    "NFCI": ("Chicago Fed", "index", "weekly_nfci"),
    "ANFCI": ("Chicago Fed", "index", "weekly_nfci"),
    "H41_TGA": ("Federal Reserve Board H.4.1", "share_of_total_assets", "weekly_h41"),
    "H41_RRP": ("Federal Reserve Board H.4.1", "share_of_total_assets", "weekly_h41"),
    "H41_RESERVES": ("Federal Reserve Board H.4.1", "share_of_total_assets", "weekly_h41"),
    "TIC_FOREIGN_TREASURY_POS": ("U.S. Treasury TIC", "USD millions", "monthly_tic"),
    "TIC_LT_FOREIGN_TREASURY_POS": ("U.S. Treasury TIC", "USD millions", "monthly_tic"),
    "SPX": ("Yahoo Finance public chart", "index", "daily_close"),
    "EU_EQUITY": ("Yahoo Finance public chart", "index", "daily_close"),
    "GOLD": ("Yahoo Finance public chart", "USD per troy ounce", "daily_close"),
}
TIER_A_SERIES = list(SERIES_META)
YAHOO_SYMBOLS = {"SPX": "%5EGSPC", "EU_EQUITY": "%5ESTOXX50E", "GOLD": "GC%3DF"}

FEATURE_SPEC = [
    {"feature": "bk5_z60", "series": "BKEVEN05", "transform": "z", "window": 60},
    {"feature": "bk10_z60", "series": "BKEVEN10", "transform": "z", "window": 60},
    {"feature": "nfci_z60", "series": "NFCI", "transform": "z", "window": 60},
    {"feature": "anfci_z60", "series": "ANFCI", "transform": "z", "window": 60},
    {"feature": "h41_tga_ratio_z252", "series": "H41_TGA", "transform": "z", "window": 252},
    {"feature": "h41_rrp_ratio_z252", "series": "H41_RRP", "transform": "z", "window": 252},
    {"feature": "h41_reserves_ratio_z252", "series": "H41_RESERVES", "transform": "z", "window": 252},
    {"feature": "tic_total_change_12m", "series": "TIC_FOREIGN_TREASURY_POS", "transform": "release_logret", "window": 12},
    {"feature": "tic_lt_change_12m", "series": "TIC_LT_FOREIGN_TREASURY_POS", "transform": "release_logret", "window": 12},
    {"feature": "spx_ret_20d", "series": "SPX", "transform": "logret", "window": 20},
    {"feature": "eu_ret_20d", "series": "EU_EQUITY", "transform": "logret", "window": 20},
    {"feature": "gold_ret_20d", "series": "GOLD", "transform": "logret", "window": 20},
]

EXPERIMENT_BLOCKS = {
    "inflation_expectations": ["bk5_z60", "bk10_z60"],
    "financial_conditions": ["nfci_z60", "anfci_z60"],
    "fed_liquidity": ["h41_tga_ratio_z252", "h41_rrp_ratio_z252", "h41_reserves_ratio_z252"],
    "tic_flows": ["tic_total_change_12m", "tic_lt_change_12m"],
    "market_proxies": ["spx_ret_20d", "eu_ret_20d", "gold_ret_20d"],
}


def _next_weekday(day: date, weekday: int) -> date:
    delta = (weekday - day.weekday()) % 7
    return day + timedelta(days=delta or 7)


def release_instant(observation: date, series_id: str) -> tuple[datetime, str]:
    """Conservative/modelled availability, never ingestion time."""
    rule = SERIES_META[series_id][2]
    if rule == "daily_close":
        return datetime.combine(observation + timedelta(days=1), time.min, UTC), "inferred_conservative"
    if rule == "weekly_nfci":
        release = _next_weekday(observation, 2)
        return datetime.combine(release, time(8, 30), NY).astimezone(UTC), "known_release_rule"
    if rule == "weekly_h41":
        release = _next_weekday(observation, 3)
        return datetime.combine(release, time(16, 30), NY).astimezone(UTC), "known_release_rule"
    if rule == "monthly_tic":
        month_end = date(observation.year, observation.month, calendar.monthrange(observation.year, observation.month)[1])
        return datetime.combine(month_end + timedelta(days=60), time(21), UTC), "inferred_conservative"
    raise ValueError(f"Unknown release rule for {series_id}")


def _http_get(name: str, url: str, *, offline: bool, refresh: bool) -> tuple[bytes, dict]:
    key = hashlib.sha256(url.encode()).hexdigest()
    registry = root() / "registry" / "tier_a" / f"{name}-{key}.json"
    if registry.exists() and (offline or not refresh):
        meta = json.loads(registry.read_text(encoding="utf-8"))
        body = (root() / meta["payload"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError(f"Cached checksum mismatch: {name}")
        return body, meta
    if offline:
        raise ValueError(f"No cached Tier A snapshot: {name}")
    headers = {"User-Agent": "FX-Causal-Lab/0.19 source-validation", "Accept": "*/*"}
    with httpx.Client(timeout=300, follow_redirects=True, transport=httpx.HTTPTransport(retries=2)) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
    meta = save_raw("tier_a_" + name, str(response.url), response.content, dict(response.headers), response.status_code)
    registry.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(registry, meta)
    return response.content, meta


def _parse_gsw(body: bytes) -> dict[str, list[tuple[date, float]]]:
    lines = body.decode("utf-8-sig").splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.lower().startswith("date,")), None)
    if header_index is None:
        raise ValueError("GSW layout changed: Date header not found")
    wanted = ("BKEVEN05", "BKEVEN10", "TIPSY05", "TIPSY10")
    out = {series: [] for series in wanted}
    for row in csv.DictReader(io.StringIO("\n".join(lines[header_index:]))):
        raw_day = (row.get("Date") or row.get("date") or "").strip()
        if not raw_day:
            continue
        day = date.fromisoformat(raw_day)
        for series in wanted:
            raw = (row.get(series) or "").strip()
            if raw.upper() in {"", "NA", "N/A", "NULL"}:
                continue
            value = float(raw)
            if math.isfinite(value):
                out[series].append((day, value))
    if any(len(out[series]) < 1000 for series in wanted):
        raise ValueError("GSW history unexpectedly short")
    return out


def _parse_nfci(body: bytes) -> dict[str, list[tuple[date, float]]]:
    out = {"NFCI": [], "ANFCI": []}
    for row in csv.DictReader(io.StringIO(body.decode("utf-8-sig"))):
        raw_day = (row.get("Friday_of_Week") or "").strip()
        if not raw_day:
            continue
        try:
            day = datetime.strptime(raw_day[:10], "%m/%d/%Y").date()
        except ValueError:
            day = date.fromisoformat(raw_day[:10])
        for series in out:
            raw = (row.get(series) or "").strip()
            if raw:
                value = float(raw)
                if math.isfinite(value):
                    out[series].append((day, value))
    if any(len(out[series]) < 1000 for series in out):
        raise ValueError("NFCI history unexpectedly short")
    return out


def _parse_h41(body: bytes) -> dict[str, list[tuple[date, float]]]:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            xml = archive.read("H41_data.xml")
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError("Invalid H.4.1 SDMX ZIP") from error
    wanted = {"RESPPLLDT_N.WW": "H41_TGA", "RESPPLLR_N.WW": "H41_RRP",
              "RESH4R_N.WW": "H41_RESERVES", "RESPPA_N.WW": "TOTAL_ASSETS"}
    raw: dict[str, dict[date, float]] = {name: {} for name in wanted.values()}
    document = ET.fromstring(xml)
    for series in document.iter():
        if not series.tag.endswith("Series") or series.attrib.get("SERIES_NAME") not in wanted:
            continue
        name = wanted[series.attrib["SERIES_NAME"]]
        for observation in series:
            if not observation.tag.endswith("Obs"):
                continue
            try:
                day = date.fromisoformat(observation.attrib["TIME_PERIOD"][:10])
                value = float(observation.attrib["OBS_VALUE"])
            except (KeyError, ValueError):
                continue
            if math.isfinite(value):
                raw[name][day] = value
    total = raw["TOTAL_ASSETS"]
    result = {}
    for name in ("H41_TGA", "H41_RRP", "H41_RESERVES"):
        result[name] = [(day, value / total[day]) for day, value in sorted(raw[name].items())
                        if day in total and total[day] > 0]
        if len(result[name]) < 500:
            raise ValueError(f"H.4.1 history unexpectedly short: {name}")
    return result


def _parse_tic(body: bytes) -> dict[str, list[tuple[date, float]]]:
    text = body.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("Country\tCountry Code\tDate\t")), None)
    if header_index is None:
        raise ValueError("TIC Table 3 header not found")
    out = {"TIC_FOREIGN_TREASURY_POS": [], "TIC_LT_FOREIGN_TREASURY_POS": []}
    for cells in csv.reader(lines[header_index + 2:], delimiter="\t"):
        if len(cells) < 7 or cells[1].strip() != "99996":
            continue
        try:
            year, month = (int(value) for value in cells[2].strip().split("-"))
            total, long_term = float(cells[3]), float(cells[5])
        except ValueError:
            continue
        day = date(year, month, calendar.monthrange(year, month)[1])
        out["TIC_FOREIGN_TREASURY_POS"].append((day, total))
        out["TIC_LT_FOREIGN_TREASURY_POS"].append((day, long_term))
    if any(len(out[series]) < 60 for series in out):
        raise ValueError("TIC Grand Total history unexpectedly short")
    return out


def _parse_yahoo(body: bytes) -> list[tuple[date, float]]:
    payload = json.loads(body)
    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        raise ValueError(f"Yahoo chart error: {(payload.get('chart') or {}).get('error')}")
    timestamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adjusted = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    closes = adjusted if len(adjusted) == len(timestamps) else quote.get("close") or []
    rows = {}
    for stamp, raw in zip(timestamps, closes):
        if raw is None:
            continue
        value = float(raw)
        if math.isfinite(value) and value > 0:
            rows[datetime.fromtimestamp(int(stamp), UTC).date()] = value
    if len(rows) < 1000:
        raise ValueError("Yahoo daily history unexpectedly short")
    return sorted(rows.items())


def _clean(rows: list[tuple[date, float]]) -> list[tuple[date, float]]:
    return sorted({day: float(value) for day, value in rows if math.isfinite(float(value))}.items())


def fetch_tier_a(offline: bool = False, refresh: bool = False) -> dict:
    """Download, preserve, normalize and report Tier A sources independently."""
    if offline and refresh:
        raise ValueError("offline and refresh are mutually exclusive")
    payloads: dict[str, list[tuple[date, float]]] = {}
    provenance: dict[str, dict] = {}
    failures: dict[str, str] = {}

    def acquire(label: str, url: str, parser, series_names: list[str]) -> None:
        try:
            body, meta = _http_get(label, url, offline=offline, refresh=refresh)
            parsed = parser(body)
            if isinstance(parsed, list):
                parsed = {series_names[0]: parsed}
            for series in series_names:
                payloads[series] = _clean(parsed[series])
                provenance[series] = meta
        except Exception as error:
            for series in series_names:
                failures[series] = f"{type(error).__name__}: {error}"

    acquire("gsw", GSW_URL, _parse_gsw, ["BKEVEN05", "BKEVEN10", "TIPSY05", "TIPSY10"])
    acquire("nfci", NFCI_URL, _parse_nfci, ["NFCI", "ANFCI"])
    acquire("h41", H41_URL, _parse_h41, ["H41_TGA", "H41_RRP", "H41_RESERVES"])
    acquire("tic", TIC_URL, _parse_tic, ["TIC_FOREIGN_TREASURY_POS", "TIC_LT_FOREIGN_TREASURY_POS"])
    for series, symbol in YAHOO_SYMBOLS.items():
        acquire(series.lower(), YAHOO_URL.format(symbol=symbol), _parse_yahoo, [series])

    signature = {name: [(str(day), value) for day, value in payloads.get(name, [])] for name in TIER_A_SERIES}
    normalized_hash = hashlib.sha256(json.dumps(signature, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    folder = root() / "silver" / "tier_a" / dataset_id
    status: dict[str, dict] = {}
    for series in TIER_A_SERIES:
        rows = payloads.get(series, [])
        if not rows:
            status[series] = {"series_id": series, "status": "UNAVAILABLE", "reason": failures.get(series, "no observations"),
                              "from": None, "to": None, "rows": 0}
            continue
        meta = provenance[series]
        observations = []
        for day, value in rows:
            available_at, time_quality = release_instant(day, series)
            observations.append({
                "observation_date": day, "event_time": datetime.combine(day, time.min, UTC), "value": value,
                "series_id": series, "provider": SERIES_META[series][0], "unit": SERIES_META[series][1],
                "frequency": SERIES_META[series][2], "published_at": None, "available_at": available_at,
                "ingested_at": meta["ingested_at"], "revision_id": None,
                "quality": "current_history_unknown_vintage", "time_quality": time_quality,
                "strict_pit_eligible": False, "source_url": meta["source_url"], "raw_payload_hash": meta["sha256"],
            })
        path = folder / f"{series.lower()}.parquet"
        _write_observations(observations, path)
        span_years = (rows[-1][0] - rows[0][0]).days / 365.25
        source_status = "READY_NON_STRICT" if span_years >= 10 else "PARTIAL_READY"
        status[series] = {"series_id": series, "status": source_status, "from": str(rows[0][0]),
                          "to": str(rows[-1][0]), "rows": len(rows), "provider": SERIES_META[series][0],
                          "unit": SERIES_META[series][1], "time_quality": observations[-1]["time_quality"],
                          "strict_pit_eligible": False, "file": path.relative_to(root()).as_posix(),
                          "raw_payload_hash": meta["sha256"]}
    report = {
        "dataset_id": dataset_id, "parser": PARSER, "normalized_sha256": normalized_hash,
        "generated_at": datetime.now(UTC).isoformat(), "series": status,
        "counts": {name: sum(row["status"] == value for row in status.values()) for name, value in
                   (("ready_non_strict", "READY_NON_STRICT"), ("partial_ready", "PARTIAL_READY"),
                    ("unavailable", "UNAVAILABLE"))},
        "strict_pit_eligible": False,
        "limitations": [
            "All downloads are current-history snapshots; historical vintages are not proven.",
            "Daily closes become usable the next day; TIC uses a conservative month-end plus 60-day delay.",
            "NFCI and H.4.1 use documented normal release rules; holiday exceptions are not reconstructed.",
            "Yahoo market proxies require a terms review before redistribution.",
        ],
        "files": {series: row["file"] for series, row in status.items() if row.get("file")},
    }
    atomic_json(root() / "reports" / "tier_a_coverage.json", report)
    return report


def _write_observations(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = path.with_suffix(f".{uuid4().hex}.json.tmp")
    parquet_tmp = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    json_tmp.write_text("\n".join(json.dumps(row, default=str, allow_nan=False) for row in rows), encoding="utf-8")
    try:
        with duckdb.connect() as connection:
            connection.execute("SET TimeZone='UTC'")
            connection.execute(
                "CREATE TABLE observations AS SELECT observation_date::DATE AS observation_date,"
                "event_time::TIMESTAMPTZ AS event_time,value::DOUBLE AS value,series_id::VARCHAR AS series_id,"
                "provider::VARCHAR AS provider,unit::VARCHAR AS unit,frequency::VARCHAR AS frequency,"
                "published_at::TIMESTAMPTZ AS published_at,available_at::TIMESTAMPTZ AS available_at,"
                "ingested_at::TIMESTAMPTZ AS ingested_at,revision_id::VARCHAR AS revision_id,"
                "quality::VARCHAR AS quality,time_quality::VARCHAR AS time_quality,"
                "strict_pit_eligible::BOOLEAN AS strict_pit_eligible,source_url::VARCHAR AS source_url,"
                "raw_payload_hash::VARCHAR AS raw_payload_hash FROM read_json_auto(?)", [str(json_tmp)])
            connection.execute("COPY observations TO ? (FORMAT PARQUET)", [str(parquet_tmp)])
        parquet_tmp.replace(path)
    finally:
        json_tmp.unlink(missing_ok=True)
        parquet_tmp.unlink(missing_ok=True)


def _load_records(path: Path) -> list[tuple[date, float, datetime]]:
    with duckdb.connect() as connection:
        connection.execute("SET TimeZone='UTC'")
        rows = connection.execute("SELECT observation_date,value,available_at FROM read_parquet(?) ORDER BY available_at", [str(path)]).fetchall()
    return [(date.fromisoformat(str(day)), float(value), available) for day, value, available in rows]


def asof_join(grid: list[date], values: list[tuple[date, float]], series_id: str) -> list[tuple[float | None, int | None]]:
    records = [(day, value, release_instant(day, series_id)[0]) for day, value in values]
    return _asof_records(grid, records)


def _asof_records(grid: list[date], records: list[tuple[date, float, datetime]]) -> list[tuple[float | None, int | None]]:
    if not records:
        return [(None, None)] * len(grid)
    ordered = sorted(records, key=lambda row: row[2])
    release_days = [available.date() for _, _, available in ordered]
    result = []
    for grid_day in grid:
        index = bisect_right(release_days, grid_day) - 1
        if index < 0:
            result.append((None, None))
        else:
            result.append((ordered[index][1], (grid_day - release_days[index]).days))
    return result


def _zscore(values: list[float | None], index: int, window: int) -> float | None:
    if index + 1 < window:
        return None
    sample = values[index - window + 1:index + 1]
    if any(value is None for value in sample):
        return None
    finite = [float(value) for value in sample]
    mean = sum(finite) / window
    variance = sum((value - mean) ** 2 for value in finite) / window
    return (finite[-1] - mean) / math.sqrt(variance) if variance > 1e-18 else 0.0


def _release_log_returns(records: list[tuple[date, float, datetime]], lag: int) -> list[tuple[date, float, datetime]]:
    return [(records[index][0], math.log(records[index][1] / records[index - lag][1]), records[index][2])
            for index in range(lag, len(records)) if records[index][1] > 0 and records[index - lag][1] > 0]


def _load_common_grid() -> tuple[list[date], dict]:
    coverage_path = root() / "reports" / "data_coverage.json"
    if not coverage_path.exists():
        raise ValueError("data_coverage.json is missing; run open-data-backfill first")
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    with duckdb.connect() as connection:
        rows = connection.execute("SELECT observation_date FROM read_parquet(?) ORDER BY observation_date",
                                  [str(root() / coverage["files"]["common_d1"])]).fetchall()
    return [date.fromisoformat(str(row[0])) for row in rows], coverage


def build_tier_a_features() -> dict:
    """Build a nullable deterministic Tier A matrix on the frozen D1 grid."""
    source_path, baseline_path = root() / "reports" / "tier_a_coverage.json", root() / "reports" / "denn_baseline.json"
    if not source_path.exists():
        raise ValueError("tier_a_coverage.json is missing; run tier-a-fetch first")
    if not baseline_path.exists():
        raise ValueError("denn_baseline.json is missing; run denn-baseline first")
    source_report = json.loads(source_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    grid, coverage = _load_common_grid()
    with duckdb.connect() as connection:
        cursor = connection.execute("SELECT * FROM read_parquet(?) ORDER BY feature_date",
                                    [str(root() / baseline["files"]["features"])] )
        columns = [description[0] for description in cursor.description]
        baseline_rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    by_day = {date.fromisoformat(str(row["feature_date"])): row for row in baseline_rows}
    matrix = [{"feature_date": day, **{key: value for key, value in by_day[day].items() if key != "feature_date"}}
              for day in grid if day in by_day]
    matrix_grid = [row["feature_date"] for row in matrix]
    feature_coverage = {}
    for spec in FEATURE_SPEC:
        relative = source_report.get("files", {}).get(spec["series"])
        if not relative:
            joined = [(None, None)] * len(matrix)
        else:
            records = _load_records(root() / relative)
            if spec["transform"] == "release_logret":
                records = _release_log_returns(records, int(spec["window"]))
            joined = _asof_records(matrix_grid, records)
        levels = [value for value, _ in joined]
        values: list[float | None] = []
        for index, value in enumerate(levels):
            if value is None:
                transformed = None
            elif spec["transform"] == "z":
                transformed = _zscore(levels, index, int(spec["window"]))
            elif spec["transform"] == "logret":
                lag = int(spec["window"])
                previous = levels[index - lag] if index >= lag else None
                transformed = math.log(float(value) / float(previous)) if previous is not None and value > 0 and previous > 0 else None
            else:
                transformed = float(value)
            values.append(transformed)
            matrix[index][spec["feature"]] = transformed
            matrix[index][f"{spec['feature']}__age_days"] = joined[index][1]
        valid = [index for index, value in enumerate(values) if value is not None]
        feature_coverage[spec["feature"]] = {"series": spec["series"], "rows": len(valid),
            "from": str(matrix_grid[valid[0]]) if valid else None, "to": str(matrix_grid[valid[-1]]) if valid else None,
            "status": "READY_NON_STRICT" if valid else "UNAVAILABLE"}
    ranges = {}
    for block, features in EXPERIMENT_BLOCKS.items():
        valid = [row["feature_date"] for row in matrix if all(row.get(feature) is not None for feature in features)]
        ranges[block] = {"required_features": features, "from": str(valid[0]) if valid else None,
                         "to": str(valid[-1]) if valid else None, "rows": len(valid),
                         "status": "READY_NON_STRICT" if valid else "BLOCKED_DATA"}
    signature_rows = [{key: (str(value) if isinstance(value, (date, datetime)) else value) for key, value in row.items()}
                      for row in matrix]
    normalized_hash = hashlib.sha256(json.dumps(signature_rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    output = root() / "gold" / "tier_a" / dataset_id / "features.parquet"
    _write_matrix(matrix, output)
    report = {"dataset_id": dataset_id, "parser": PARSER, "normalized_sha256": normalized_hash,
              "generated_at": datetime.now(UTC).isoformat(), "input_dataset_id": coverage["dataset_id"],
              "tier_a_source_dataset_id": source_report["dataset_id"], "rows": len(matrix),
              "date_from": str(matrix_grid[0]), "date_to": str(matrix_grid[-1]),
              "feature_coverage": feature_coverage, "experiment_ranges": ranges,
              "strict_pit_eligible": False, "files": {"features": output.relative_to(root()).as_posix()},
              "limitations": ["Current-history, non-strict matrix; historical vintages are not proven.",
                              "Sparse values appear only after modeled availability; pre-release rows remain NULL.",
                              "Experiments use declared block-specific ranges; missing features are not imputed."]}
    atomic_json(root() / "reports" / "tier_a_features.json", report)
    return report


def _write_matrix(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = path.with_suffix(f".{uuid4().hex}.json.tmp")
    parquet_tmp = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    json_tmp.write_text("\n".join(json.dumps(row, default=str, allow_nan=False) for row in rows), encoding="utf-8")
    try:
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE features AS SELECT * FROM read_json_auto(?)", [str(json_tmp)])
            connection.execute("ALTER TABLE features ALTER feature_date TYPE DATE")
            connection.execute("COPY features TO ? (FORMAT PARQUET)", [str(parquet_tmp)])
        parquet_tmp.replace(path)
    finally:
        json_tmp.unlink(missing_ok=True)
        parquet_tmp.unlink(missing_ok=True)
