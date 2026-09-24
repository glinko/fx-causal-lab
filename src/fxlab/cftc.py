"""CFTC Traders in Financial Futures positioning with release-lag guards."""
import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from .macro import write_macro_parquet
from .store import atomic_json, root, save_raw

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
EUR_CODE = "099741"
PARSER = "cftc-tff-futures-only-1"
SCHEDULE_URL = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm"
ANNUAL_URL = "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
MONTHS = {name: number for number, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
CATEGORIES = {
    "dealer": "Dealer",
    "asset_manager": "Asset_Mgr",
    "leveraged_funds": "Lev_Money",
    "other_reportables": "Other_Rept",
}


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.text = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = tag
            self.text = []

    def handle_data(self, data):
        if self.cell is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join(" ".join(self.text).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def release_schedule(body: bytes) -> list[date]:
    text = body.decode("utf-8", errors="replace")
    year_match = re.search(r"(20\d{2}) Release Schedule", text, re.IGNORECASE)
    if not year_match:
        raise ValueError("CFTC release schedule year not found")
    year = int(year_match.group(1))
    parser = TableParser()
    parser.feed(text)
    dates = []
    for row in parser.rows:
        if not row or row[0] not in MONTHS:
            continue
        for cell in row[1:]:
            for number in re.findall(r"\b(\d{1,2})(?:\*)?\b", cell):
                dates.append(date(year, MONTHS[row[0]], int(number)))
    if len(dates) < 40 or len(set(dates)) != len(dates):
        raise ValueError("CFTC release schedule is incomplete or duplicated")
    return sorted(dates)


def scheduled_for(report_date: date, schedule: list[date]) -> datetime | None:
    candidates = [day for day in schedule if report_date < day <= report_date + timedelta(days=10)]
    return datetime.combine(candidates[0], time(15, 30), NY) if candidates else None


def integer(row: dict, field: str) -> int:
    value = row.get(field, "").strip().replace(",", "")
    if not re.fullmatch(r"-?\d+", value):
        raise ValueError(f"Invalid CFTC integer field {field}")
    return int(value)


def normalize_tff(body: bytes, meta: dict, start: date, end: date, schedule: list[date]) -> list[dict]:
    if hashlib.sha256(body).hexdigest() != meta["sha256"]:
        raise ValueError("CFTC raw checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith((".txt", ".csv"))]
        if len(names) != 1:
            raise ValueError("Expected one CFTC text table")
        rows = csv.DictReader(io.StringIO(archive.read(names[0]).decode("utf-8-sig")))
        selected = []
        for original in rows:
            row = {key.strip(): (value or "").strip() for key, value in original.items()}
            code = row.get("CFTC_Contract_Market_Code", "").strip('"')
            if code != EUR_CODE:
                continue
            report_date = date.fromisoformat(row["Report_Date_as_YYYY-MM-DD"])
            if not start <= report_date <= end:
                continue
            if "FutOnly" not in row.get("FutOnly_or_Combined", ""):
                raise ValueError("Expected Futures Only CFTC row")
            open_interest = integer(row, "Open_Interest_All")
            if open_interest <= 0:
                raise ValueError("CFTC open interest must be positive")
            planned = scheduled_for(report_date, schedule)
            result = {
                "observation_id": f"cftc-tff-eur-{report_date}", "source": "cftc", "instrument": "EUR_FX_FUTURES",
                "contract_market_code": EUR_CODE, "market_name": row["Market_and_Exchange_Names"],
                "report_date": str(report_date), "event_time": datetime.combine(report_date, time(0), UTC),
                "scheduled_release_at": planned, "published_at": None,
                "available_at": datetime.combine(planned.date() + timedelta(days=1), time(0), NY) if planned else None,
                "time_quality": "inferred_conservative" if planned else "unknown",
                "strict_pit_eligible": False, "open_interest": open_interest,
                "source_url": meta["source_url"], "raw_payload_hash": meta["sha256"], "ingested_at": meta["ingested_at"],
            }
            for name, prefix in CATEGORIES.items():
                long_value = integer(row, f"{prefix}_Positions_Long_All")
                short_value = integer(row, f"{prefix}_Positions_Short_All")
                spread_value = integer(row, f"{prefix}_Positions_Spread_All")
                if min(long_value, short_value, spread_value) < 0:
                    raise ValueError("CFTC positions must be non-negative")
                result.update({f"{name}_long": long_value, f"{name}_short": short_value,
                               f"{name}_spread": spread_value, f"{name}_net": long_value - short_value,
                               f"{name}_net_share_oi": (long_value - short_value) / open_interest})
            non_long = integer(row, "NonRept_Positions_Long_All")
            non_short = integer(row, "NonRept_Positions_Short_All")
            if min(non_long, non_short) < 0:
                raise ValueError("CFTC non-reportable positions must be non-negative")
            result.update(nonreportable_long=non_long, nonreportable_short=non_short,
                          nonreportable_net=non_long - non_short,
                          nonreportable_net_share_oi=(non_long - non_short) / open_interest)
            selected.append(result)
    if not selected:
        return []
    selected.sort(key=lambda row: row["report_date"])
    if len({row["report_date"] for row in selected}) != len(selected):
        raise ValueError("Duplicate EUR CFTC report dates")
    return selected


def fetch_cftc_snapshots(start: date, end: date) -> dict:
    if end < start:
        raise ValueError("end precedes start")
    snapshots = []
    with httpx.Client(timeout=60, follow_redirects=True, transport=httpx.HTTPTransport(retries=2),
                      headers={"User-Agent": "FXCausalLab/0.4 research"}) as client:
        response = client.get(SCHEDULE_URL)
        schedule_meta = save_raw("cftc_release_schedule", str(response.url), response.content,
                                 dict(response.headers), response.status_code)
        response.raise_for_status()
        release_schedule(response.content)
        snapshots.append(schedule_meta)
        years = range(start.year, end.year + 1)
        annual = []
        for year in years:
            response = client.get(ANNUAL_URL.format(year=year))
            meta = save_raw("cftc_tff", str(response.url), response.content, dict(response.headers), response.status_code)
            response.raise_for_status()
            annual.append({"year": year, "snapshot": meta})
            snapshots.append(meta)
    manifest = {"provider": "cftc_tff_futures_only", "requested_start": str(start), "requested_end": str(end),
                "schedule": schedule_meta, "annual": annual, "snapshots": snapshots,
                "fetched_at": datetime.now(UTC).isoformat()}
    atomic_json(root()/"reports"/"cftc_fetch.json", manifest)
    return manifest


def replay_cftc(manifest: Path | dict) -> dict:
    source = json.loads(manifest.read_text(encoding="utf-8")) if isinstance(manifest, Path) else manifest
    schedule_meta = source["schedule"]
    schedule_body = (root()/schedule_meta["payload"]).read_bytes()
    if hashlib.sha256(schedule_body).hexdigest() != schedule_meta["sha256"]:
        raise ValueError("CFTC schedule checksum mismatch")
    schedule = release_schedule(schedule_body)
    start, end = date.fromisoformat(source["requested_start"]), date.fromisoformat(source["requested_end"])
    rows = []
    for item in source["annual"]:
        meta = item["snapshot"]
        body = (root()/meta["payload"]).read_bytes()
        rows.extend(normalize_tff(body, meta, start, end, schedule))
    rows.sort(key=lambda row: row["report_date"])
    if not rows or len({row["report_date"] for row in rows}) != len(rows):
        raise ValueError("CFTC replay produced no rows or duplicates")
    normalized = [{key: value for key, value in row.items() if key != "ingested_at"} for row in rows]
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    signature = {"parser": PARSER, "normalized_sha256": normalized_sha256,
                 "snapshots": [(m["source_url"], m["sha256"]) for m in source["snapshots"]]}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root()/"silver"/"cftc"/dataset_id
    write_macro_parquet(rows, folder/"eur_tff.parquet",
                        ("event_time", "scheduled_release_at", "published_at", "available_at"))
    report = {"dataset_id": dataset_id, "parser": PARSER, "provider": source["provider"],
              "requested_start": source["requested_start"], "requested_end": source["requested_end"],
              "rows": len(rows), "first_report_date": rows[0]["report_date"], "last_report_date": rows[-1]["report_date"],
              "scheduled_rows": sum(row["scheduled_release_at"] is not None for row in rows),
              "unknown_availability_rows": sum(row["available_at"] is None for row in rows),
              "non_tuesday_rows": sum(datetime.fromisoformat(row["report_date"]).weekday() != 1 for row in rows),
              "normalized_sha256": normalized_sha256,
              "files": {"positions": str((folder/"eur_tff.parquet").relative_to(root()))},
              "snapshots": source["snapshots"], "strict_pit_eligible": False,
              "limitations": [
                  "Report date describes Tuesday positions and is never used as feature availability.",
                  "The preserved CFTC schedule is tentative. scheduled_release_at is not proof of actual publication.",
                  "Only rows covered by the preserved schedule receive a conservative available_at at 00:00 ET after the scheduled release day.",
                  "Older rows keep available_at null until historical release dates are recovered.",
                  "Futures Only TFF for contract 099741; no options positions or roll adjustment.",
              ], "generated_at": datetime.now(UTC).isoformat()}
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"cftc.json", report)
    return report


def backfill_cftc(start: date, end: date) -> dict:
    return replay_cftc(fetch_cftc_snapshots(start, end))
