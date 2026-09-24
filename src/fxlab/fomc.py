"""Archived FOMC statements with exact release timestamps and immutable replay."""
import hashlib
import json
import re
from datetime import date, datetime, time, timezone
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from .macro import html_text, write_macro_parquet
from .store import atomic_json, root, save_raw

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FED_BASE = "https://www.federalreserve.gov"
PARSER = "fomc-statement-html-1"
STATEMENT_PATH = re.compile(r"/newsevents/pressreleases/monetary(20\d{6})a\.htm$")
RATE = r"(\d+(?:-\d+/\d+|\.\d+)?)"


class HrefParser(HTMLParser):
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
            self.links.append((self.href, " ".join(" ".join(self.parts).split())))
            self.href = None
            self.parts = []


def statement_links(body: bytes, start: date, end: date) -> list[dict]:
    parser = HrefParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    found = {}
    for href, label in parser.links:
        if label.upper() != "HTML":
            continue
        path = href.split("?", 1)[0]
        match = STATEMENT_PATH.search(path)
        if not match:
            continue
        decision_date = datetime.strptime(match.group(1), "%Y%m%d").date()
        if start <= decision_date <= end:
            url = urljoin(FED_BASE, path)
            found[url] = {"decision_date": str(decision_date), "url": url}
    return sorted(found.values(), key=lambda row: row["decision_date"])


def release_timestamp(text: str) -> datetime:
    day_match = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(20\d{2})\b", text)
    time_match = re.search(r"For release at\s+(\d{1,2}:\d{2})\s*([ap])\.m\.\s+(E[DS]T)\b", text, re.IGNORECASE)
    if not day_match or not time_match:
        raise ValueError("FOMC release date or time not found")
    day = datetime.strptime(" ".join(day_match.groups()), "%B %d %Y").date()
    clock = datetime.strptime(f"{time_match.group(1)} {time_match.group(2).upper()}M", "%I:%M %p").time()
    stamp = datetime.combine(day, clock, NY)
    if stamp.tzname().upper() != time_match.group(3).upper():
        raise ValueError("FOMC timezone label disagrees with New York calendar")
    return stamp


def rate_value(value: str) -> float:
    if "-" in value and "/" in value:
        whole, fraction = value.split("-", 1)
        return float(int(whole) + Fraction(fraction))
    return float(value)


def target_range(text: str) -> tuple[float, float]:
    text = text.translate(str.maketrans({"‑": "-", "–": "-", "−": "-"}))
    match = re.search(
        rf"target range for the federal funds rate(?:(?!\.\s).){{0,120}}?(?:at|,\s*to|\s+to)\s+{RATE}\s+to\s+{RATE}\s+percent",
        text, re.IGNORECASE,
    )
    if not match:
        raise ValueError("FOMC target range not found")
    lower, upper = rate_value(match.group(1)), rate_value(match.group(2))
    if not 0 <= lower <= upper <= 30:
        raise ValueError("Invalid FOMC target range")
    return lower, upper


def normalize_statement(body: bytes, link: dict, meta: dict) -> dict:
    if hashlib.sha256(body).hexdigest() != meta["sha256"]:
        raise ValueError("FOMC raw checksum mismatch")
    text = html_text(body)
    published = release_timestamp(text)
    decision_date = date.fromisoformat(link["decision_date"])
    if published.date() != decision_date:
        raise ValueError("FOMC URL date disagrees with statement date")
    lower, upper = target_range(text)
    return {
        "event_id": f"fomc-statement-{decision_date}", "source": "federal_reserve",
        "event_type": "fomc_statement", "decision_date": str(decision_date),
        "event_time": published, "scheduled_release_at": published, "published_at": published,
        "published_time_quality": "exact_timestamp", "available_at": None, "time_quality": "unknown",
        "strict_pit_eligible": False, "target_lower": lower, "target_upper": upper,
        "target_midpoint": (lower + upper) / 2, "previous_midpoint": None, "rate_change_bp": None,
        "consensus": None, "forecast_vintage": None, "surprise": None,
        "source_url": link["url"], "raw_payload_hash": meta["sha256"], "ingested_at": meta["ingested_at"],
    }


def fetch_fomc_snapshots(start: date, end: date) -> dict:
    if end < start:
        raise ValueError("end precedes start")
    snapshots, statements = [], []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FXCausalLab/0.5; research; +local)"}
    with httpx.Client(headers=headers, timeout=60, follow_redirects=True,
                      transport=httpx.HTTPTransport(retries=2)) as client:
        response = client.get(CALENDAR_URL)
        calendar = save_raw("fomc_calendar", str(response.url), response.content, dict(response.headers), response.status_code)
        response.raise_for_status()
        links = statement_links(response.content, start, end)
        if not links:
            raise ValueError("No FOMC statements in requested period")
        snapshots.append(calendar)
        for link in links:
            response = client.get(link["url"])
            meta = save_raw("fomc_statement", str(response.url), response.content, dict(response.headers), response.status_code)
            response.raise_for_status()
            normalize_statement(response.content, link, meta)
            snapshots.append(meta)
            statements.append({"link": link, "snapshot": meta})
    manifest = {"provider": "federal_reserve_fomc", "requested_start": str(start), "requested_end": str(end),
                "calendar": calendar, "statements": statements, "snapshots": snapshots,
                "fetched_at": datetime.now(UTC).isoformat()}
    atomic_json(root()/"reports"/"fomc_fetch.json", manifest)
    return manifest


def replay_fomc(manifest: Path | dict) -> dict:
    source = json.loads(manifest.read_text(encoding="utf-8")) if isinstance(manifest, Path) else manifest
    for meta in source["snapshots"]:
        body = (root()/meta["payload"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError("FOMC snapshot checksum mismatch")
    rows = [normalize_statement((root()/item["snapshot"]["payload"]).read_bytes(), item["link"], item["snapshot"])
            for item in source["statements"]]
    rows.sort(key=lambda row: row["published_at"])
    if not rows or len({row["event_id"] for row in rows}) != len(rows):
        raise ValueError("FOMC replay produced no rows or duplicates")
    for previous, current in zip(rows, rows[1:]):
        current["previous_midpoint"] = previous["target_midpoint"]
        current["rate_change_bp"] = round((current["target_midpoint"] - previous["target_midpoint"]) * 100, 8)
    normalized = [{key: value for key, value in row.items() if key != "ingested_at"} for row in rows]
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    signature = {"parser": PARSER, "normalized_sha256": normalized_sha256,
                 "snapshots": [(m["source_url"], m["sha256"]) for m in source["snapshots"]]}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root()/"silver"/"fomc"/dataset_id
    write_macro_parquet(rows, folder/"statements.parquet",
                        ("event_time", "scheduled_release_at", "published_at", "available_at", "forecast_vintage"))
    report = {"dataset_id": dataset_id, "parser": PARSER, "provider": source["provider"],
              "requested_start": source["requested_start"], "requested_end": source["requested_end"],
              "rows": len(rows), "first_published_at": rows[0]["published_at"].isoformat(),
              "last_published_at": rows[-1]["published_at"].isoformat(),
              "holds": sum(row["rate_change_bp"] == 0 for row in rows),
              "cuts": sum(row["rate_change_bp"] is not None and row["rate_change_bp"] < 0 for row in rows),
              "hikes": sum(row["rate_change_bp"] is not None and row["rate_change_bp"] > 0 for row in rows),
              "unknown_availability_rows": sum(row["available_at"] is None for row in rows),
              "normalized_sha256": normalized_sha256,
              "files": {"statements": str((folder/"statements.parquet").relative_to(root()))},
              "snapshots": source["snapshots"], "strict_pit_eligible": False,
              "limitations": [
                  "Each statement supplies an exact release time and target range; the archived page may later be updated.",
                  "The release timestamp populates published_at, but historical system receipt is not proven, so available_at remains null.",
                  "The first row has no previous midpoint because changes are derived only inside the requested window.",
                  "Consensus, forecast vintage and policy surprise are unavailable and remain null.",
              ], "generated_at": datetime.now(UTC).isoformat()}
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"fomc.json", report)
    return report


def backfill_fomc(start: date, end: date) -> dict:
    return replay_fomc(fetch_fomc_snapshots(start, end))
