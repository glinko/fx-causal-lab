"""Official macro release archives with explicit historical-availability limits."""
import hashlib
import html
import json
import re
import time
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from uuid import uuid4

import httpx

from .store import atomic_json, root, save_raw

UTC = timezone.utc
BLS_BASE = "https://www.bls.gov"
BLS_ARCHIVES = {
    "cpi": ("https://www.bls.gov/bls/news-release/cpi.htm", "Consumer Price Index"),
    "empsit": ("https://www.bls.gov/bls/news-release/empsit.htm", "Employment Situation"),
}
MONTHS = {name: number for number, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
USER_AGENT = "Mozilla/5.0 (compatible; FXCausalLab/0.3; research; +local)"
PARSER = "bls-release-html-1"


class LinkTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.parts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self.href = dict(attrs).get("href")
            self.parts = []

    def handle_data(self, data):
        if self.href is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.href is not None:
            self.links.append((self.href, html.unescape(" ".join(" ".join(self.parts).split()))))
            self.href = None
            self.parts = []


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def html_text(body: bytes) -> str:
    parser = TextParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    return html.unescape(" ".join(" ".join(parser.parts).split()))


def archive_links(body: bytes, kind: str) -> list[dict]:
    if kind not in BLS_ARCHIVES:
        raise ValueError("Unsupported BLS archive")
    parser = LinkTextParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    suffix = BLS_ARCHIVES[kind][1]
    found = []
    for href, label in parser.links:
        match = re.fullmatch(r"(January|February|March|April|May|June|July|August|September|October|November|December) (\d{4}) " + re.escape(suffix), label)
        if not match or "/news.release/archives/" not in href or not href.lower().endswith(".htm"):
            continue
        period = date(int(match.group(2)), MONTHS[match.group(1)], 1)
        found.append({"kind": kind, "observation_period": period.strftime("%Y-%m"),
                      "url": urljoin(BLS_BASE, href), "label": label})
    unique = {row["url"]: row for row in found}
    return sorted(unique.values(), key=lambda row: row["observation_period"])


def release_timestamp(text: str) -> datetime:
    from zoneinfo import ZoneInfo
    match = re.search(
        r"embargoed until\s+(?:USDL-[0-9-]+\s+)?(\d{1,2}:\d{2})\s*([ap]\.m\.)\s*\(ET\)\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*([A-Z][a-z]+ \d{1,2}, \d{4})",
        text, re.IGNORECASE,
    )
    if not match:
        raise ValueError("BLS embargo timestamp not found")
    clock = datetime.strptime(match.group(1) + " " + match.group(2).replace(".", "").upper(), "%I:%M %p").time()
    day = datetime.strptime(match.group(3), "%B %d, %Y").date()
    return datetime.combine(day, clock, ZoneInfo("America/New_York"))


def signed_change(action: str, value: str | None) -> float:
    action = action.lower()
    if "unchanged" in action:
        return 0.0
    if value is None:
        raise ValueError("Change value missing")
    number = float(value.replace(",", ""))
    return -number if action in {"declined", "decreased", "fell"} else number


def extract_actuals(kind: str, text: str) -> list[dict]:
    if kind == "cpi":
        two_month = []
        two_month_patterns = [
            ("US_CPI_ALL_2M_SA", r"Consumer Price Index for All Urban Consumers \(CPI-U\) (rose|increased|advanced|declined|decreased|fell)(?: by)? ([0-9.]+) percent on a seasonally adjusted basis over the 2 months"),
            ("US_CPI_CORE_2M_SA", r"seasonally adjusted index for all items less food and energy (rose|increased|advanced|declined|decreased|fell)(?: by)? ([0-9.]+) percent over the 2 months"),
        ]
        for indicator, pattern in two_month_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                two_month.append({"indicator": indicator, "actual": signed_change(match.group(1), match.group(2)), "unit": "percent_2m"})
        if two_month:
            return two_month
        rows = []
        patterns = [
            ("US_CPI_ALL_MOM_SA", r"Consumer Price Index for All Urban Consumers \(CPI-U\) (rose|increased|advanced|declined|decreased|fell|was unchanged|remained unchanged)(?: by)?(?: ([0-9.]+) percent)?(?: in [A-Z][a-z]+)? on a seasonally adjusted basis"),
            ("US_CPI_CORE_MOM_SA", r"index for all items less food and energy (rose|increased|advanced|declined|decreased|fell|was unchanged|remained unchanged)(?: by)?(?: ([0-9.]+) percent)?(?: in [A-Z][a-z]+| over the month)"),
        ]
        for indicator, pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                rows.append({"indicator": indicator, "actual": signed_change(match.group(1), match.group(2)), "unit": "percent"})
        return rows
    if kind == "empsit":
        patterns = [
            r"Total nonfarm payroll employment (rose|increased|grew|edged up|declined|decreased|fell|edged down)(?: by)? ([0-9,]+)",
            r"Total nonfarm payroll employment (changed little|was little changed|was essentially unchanged)(?: in [A-Z][a-z]+)? \(([+-][0-9,]+)\)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                value = float(match.group(2).replace(",", ""))
                if match.group(1).lower() in {"declined", "decreased", "fell", "edged down"}:
                    value *= -1
                return [{"indicator": "US_NFP_CHANGE", "actual": value / 1000, "unit": "thousand_persons"}]
        return []
    raise ValueError("Unsupported release type")


def normalize_release(body: bytes, link: dict, meta: dict) -> tuple[dict, list[dict]]:
    text = html_text(body)
    published = release_timestamp(text)
    release_id = hashlib.sha256((link["kind"] + "|" + link["observation_period"] + "|" + meta["sha256"]).encode()).hexdigest()[:24]
    event = {
        "release_id": release_id, "source": "bls", "release_type": link["kind"],
        "observation_period": link["observation_period"], "scheduled_release_at": published,
        "published_at": published, "available_at": None, "time_quality": "unknown",
        "historical_release_payload": True, "strict_pit_eligible": False,
        "source_url": link["url"], "raw_payload_hash": meta["sha256"], "ingested_at": meta["ingested_at"],
    }
    observations = []
    for number, actual in enumerate(extract_actuals(link["kind"], text)):
        observations.append({
            "observation_id": f"{release_id}-{number}", "release_id": release_id, **actual,
            "observation_period": link["observation_period"], "published_at": published,
            "available_at": None, "time_quality": "unknown", "consensus": None,
            "previous": None, "revised_previous": None, "surprise": None,
            "normalized_surprise": None, "forecast_vintage": None, "revision_number": 0,
            "source": "bls_archive", "source_url": link["url"], "raw_payload_hash": meta["sha256"],
            "ingested_at": meta["ingested_at"], "historical_release_payload": True,
            "strict_pit_eligible": False,
        })
    return event, observations


def write_macro_parquet(rows: list[dict], path: Path, timestamp_columns: tuple[str, ...]):
    import duckdb
    if not rows:
        raise ValueError("Cannot write empty macro dataset")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_suffix(f".{uuid4().hex}.json.tmp")
    staged = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    raw.write_text("\n".join(json.dumps(row, default=str, allow_nan=False) for row in rows), encoding="utf-8")
    try:
        replacements = ", ".join(f"{column}::TIMESTAMPTZ AS {column}" for column in timestamp_columns)
        select = f"SELECT * REPLACE ({replacements}) FROM read_json_auto(?)" if replacements else "SELECT * FROM read_json_auto(?)"
        with duckdb.connect() as con:
            con.execute("SET TimeZone='UTC'")
            con.execute("CREATE TABLE items AS " + select, [str(raw)])
            con.execute("COPY items TO ? (FORMAT PARQUET)", [str(staged)])
        staged.replace(path)
    finally:
        raw.unlink(missing_ok=True)
        staged.unlink(missing_ok=True)


def fetch(client: httpx.Client, source: str, url: str) -> tuple[bytes, dict]:
    response = client.get(url)
    meta = save_raw(source, str(response.url), response.content, dict(response.headers), response.status_code)
    response.raise_for_status()
    return response.content, meta


def fetch_bls_snapshots(start: date, end: date) -> dict:
    if end < start:
        raise ValueError("end precedes start")
    first_month, last_month = start.strftime("%Y-%m"), end.strftime("%Y-%m")
    snapshots, index_snapshots, selected = [], [], []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True,
                      transport=httpx.HTTPTransport(retries=2)) as client:
        for kind, (index_url, _) in BLS_ARCHIVES.items():
            body, index_meta = fetch(client, f"bls_{kind}_index", index_url)
            snapshots.append(index_meta)
            index_snapshots.append(index_meta)
            links = [row for row in archive_links(body, kind) if first_month <= row["observation_period"] <= last_month]
            for link in links:
                release_body, meta = fetch(client, f"bls_{kind}_release", link["url"])
                snapshots.append(meta)
                selected.append({"link": link, "snapshot": meta})
                time.sleep(0.15)
    if not selected:
        raise ValueError("No BLS releases in requested period")
    manifest = {"provider": "bls_archive", "requested_start": str(start), "requested_end": str(end),
                "indexes": index_snapshots, "releases": selected, "snapshots": snapshots,
                "fetched_at": datetime.now(UTC).isoformat()}
    # indexes is informational; snapshots is the complete immutable input list.
    atomic_json(root() / "reports" / "bls_fetch.json", manifest)
    return manifest


def replay_bls_releases(manifest: Path | dict) -> dict:
    source = json.loads(manifest.read_text(encoding="utf-8")) if isinstance(manifest, Path) else manifest
    events, observations = [], []
    snapshots = source["snapshots"]
    for item in source["releases"]:
        meta = item["snapshot"]
        body = (root() / meta["payload"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError("BLS raw checksum mismatch")
        event, actuals = normalize_release(body, item["link"], meta)
        events.append(event)
        observations.extend(actuals)
    events.sort(key=lambda row: (row["published_at"], row["release_type"]))
    observations.sort(key=lambda row: (row["published_at"], row["indicator"]))
    if len({row["release_id"] for row in events}) != len(events):
        raise ValueError("Duplicate release IDs")
    normalized = {
        "events": [{key: value for key, value in row.items() if key != "ingested_at"} for row in events],
        "observations": [{key: value for key, value in row.items() if key != "ingested_at"} for row in observations],
    }
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    signature = {"parser": PARSER, "normalized_sha256": normalized_sha256,
                 "snapshots": [(m["source_url"], m["sha256"]) for m in snapshots]}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root() / "silver" / "macro" / dataset_id
    write_macro_parquet(events, folder / "releases.parquet", ("scheduled_release_at", "published_at", "available_at"))
    if observations:
        write_macro_parquet(observations, folder / "observations.parquet", ("published_at", "available_at", "forecast_vintage"))
    periods = sorted({row["observation_period"] for row in events})
    cursor = datetime.strptime(periods[0], "%Y-%m").date()
    last_period = datetime.strptime(periods[-1], "%Y-%m").date()
    expected_periods = []
    while cursor <= last_period:
        expected_periods.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    missing_periods = {kind: [period for period in expected_periods if not any(
        row["release_type"] == kind and row["observation_period"] == period for row in events)] for kind in BLS_ARCHIVES}
    monthly_indicators = ("US_CPI_ALL_MOM_SA", "US_CPI_CORE_MOM_SA", "US_NFP_CHANGE")
    observation_gaps = {indicator: [period for period in expected_periods if not any(
        row["indicator"] == indicator and row["observation_period"] == period for row in observations)]
        for indicator in monthly_indicators}
    report = {
        "dataset_id": dataset_id, "parser": PARSER, "normalized_sha256": normalized_sha256,
        "provider": "bls_archive", "requested_start": source["requested_start"], "requested_end": source["requested_end"],
        "release_rows": len(events), "observation_rows": len(observations),
        "release_types": {kind: sum(row["release_type"] == kind for row in events) for kind in BLS_ARCHIVES},
        "missing_periods": missing_periods,
        "observation_gaps": observation_gaps,
        "indicators": {indicator: sum(row["indicator"] == indicator for row in observations)
                       for indicator in sorted({row["indicator"] for row in observations})},
        "first_published_at": events[0]["published_at"].isoformat(), "last_published_at": events[-1]["published_at"].isoformat(),
        "files": {"releases": str((folder / "releases.parquet").relative_to(root())),
                  "observations": str((folder / "observations.parquet").relative_to(root())) if observations else None},
        "snapshots": snapshots, "strict_pit_eligible": False,
        "limitations": [
            "Archived release pages preserve first-release wording, but may contain later corrections or reissues.",
            "Embargo timestamps populate published_at; historical available_at remains unknown.",
            "Consensus and forecast vintages are unavailable and remain null; surprise is not calculated.",
            "Headline parsers cover CPI all-items/core monthly percent changes and total NFP change only.",
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder / "manifest.json", report)
    atomic_json(root() / "reports" / "macro_releases.json", report)
    return report


def backfill_bls_releases(start: date, end: date) -> dict:
    return replay_bls_releases(fetch_bls_snapshots(start, end))
