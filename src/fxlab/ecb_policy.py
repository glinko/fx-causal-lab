"""ECB monetary-policy decisions from the official FOEDB publication index."""
import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from .macro import html_text, write_macro_parquet
from .store import atomic_json, root, save_raw

UTC = timezone.utc
FRANKFURT = ZoneInfo("Europe/Berlin")
ECB_BASE = "https://www.ecb.europa.eu"
FOEDB_BASE = ECB_BASE + "/foedb/dbs/foedb/publications.en"
VERSIONS_URL = FOEDB_BASE + "/versions.json"
PARSER = "ecb-policy-foedb-html-1"
DECISION_PATH = re.compile(r"/press/pr/date/(20\d{2})/html/ecb\.mp(\d{6})~[a-z0-9]+\.en\.html$")
RATE_NAMES = {
    "mro": "main refinancing operations",
    "marginal_lending": "marginal lending facility",
    "deposit": "deposit facility",
}


def version_descriptor(body: bytes) -> dict:
    value = json.loads(body)
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict) or not re.fullmatch(r"\d+", str(value.get("version", ""))) or not re.fullmatch(r"[A-Za-z0-9]+", value.get("hash", "")):
        raise ValueError("Invalid ECB FOEDB version descriptor")
    return value


def decode_records(body: bytes, metadata: dict) -> list[dict]:
    values = json.loads(body)
    header = metadata.get("header")
    if not isinstance(values, list) or not isinstance(header, list) or not header or len(values) % len(header):
        raise ValueError("Invalid ECB FOEDB chunk")
    return [dict(zip(header, values[index:index + len(header)])) for index in range(0, len(values), len(header))]


def decision_links(chunks: list[bytes], metadata: dict, start: date, end: date) -> list[dict]:
    found = {}
    for body in chunks:
        for record in decode_records(body, metadata):
            properties = record.get("publicationProperties") or {}
            if properties.get("Title") != "Monetary policy decisions":
                continue
            timestamp = record.get("pub_timestamp")
            if not isinstance(timestamp, int):
                raise ValueError("ECB publication timestamp missing")
            published = datetime.fromtimestamp(timestamp, UTC).astimezone(FRANKFURT)
            for path in record.get("documentTypes") or []:
                match = DECISION_PATH.fullmatch(path)
                if not match:
                    continue
                decision_date = datetime.strptime(match.group(2), "%y%m%d").date()
                if decision_date != published.date():
                    raise ValueError("ECB FOEDB date disagrees with document URL")
                if start <= decision_date <= end:
                    url = urljoin(ECB_BASE, path)
                    found[url] = {"decision_date": str(decision_date), "published_at": published.isoformat(), "url": url}
    return sorted(found.values(), key=lambda row: row["decision_date"])


def key_rates(text: str) -> dict[str, float]:
    normalized = text.replace("\u00a0", " ").replace("‑", "-").replace("–", "-")
    lowered_text = normalized.lower()
    for match in re.finditer(r"\brespectively\b", normalized, re.IGNORECASE):
        start = max(0, match.start() - 800)
        window = normalized[start:match.end()]
        lowered = lowered_text[start:match.end()]
        positions = {name: lowered.rfind(label) for name, label in RATE_NAMES.items()}
        if min(positions.values()) < 0:
            continue
        segment = window[min(positions.values()):]
        values = [float(value.replace(",", ".")) for value in re.findall(r"(\d+(?:[.,]\d+)?)\s*%", segment)]
        if len(values) != 3:
            continue
        order = sorted(positions, key=positions.get)
        return dict(zip(order, values))
    raise ValueError("ECB three-rate decision not found")


def effective_date(text: str) -> str | None:
    match = re.search(r"with effect from\s+(\d{1,2}\s+[A-Z][a-z]+\s+20\d{2})", text)
    return str(datetime.strptime(match.group(1), "%d %B %Y").date()) if match else None


def normalize_decision(body: bytes, link: dict, meta: dict) -> dict:
    if hashlib.sha256(body).hexdigest() != meta["sha256"]:
        raise ValueError("ECB policy raw checksum mismatch")
    text = html_text(body)
    if "Monetary policy decisions" not in text:
        raise ValueError("ECB policy page title missing")
    rates = key_rates(text)
    published = datetime.fromisoformat(link["published_at"])
    decision_date = date.fromisoformat(link["decision_date"])
    if published.date() != decision_date or published.astimezone(FRANKFURT).strftime("%H:%M") != "14:15":
        raise ValueError("ECB publication timestamp is not the expected 14:15 Frankfurt release")
    return {
        "event_id": f"ecb-policy-{decision_date}", "source": "ecb", "event_type": "ecb_policy_decision",
        "decision_date": str(decision_date), "event_time": published, "scheduled_release_at": published,
        "published_at": published, "published_time_quality": "exact_timestamp", "available_at": None,
        "time_quality": "unknown", "strict_pit_eligible": False, "effective_date": effective_date(text),
        "deposit_rate": rates["deposit"], "mro_rate": rates["mro"], "marginal_lending_rate": rates["marginal_lending"],
        "previous_deposit_rate": None, "deposit_change_bp": None,
        "consensus": None, "forecast_vintage": None, "surprise": None,
        "source_url": link["url"], "raw_payload_hash": meta["sha256"], "ingested_at": meta["ingested_at"],
    }


def fetch(client: httpx.Client, source: str, url: str) -> tuple[bytes, dict]:
    response = client.get(url)
    meta = save_raw(source, str(response.url), response.content, dict(response.headers), response.status_code)
    response.raise_for_status()
    return response.content, meta


def fetch_ecb_policy_snapshots(start: date, end: date) -> dict:
    if end < start:
        raise ValueError("end precedes start")
    snapshots, chunk_items, releases = [], [], []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; FXCausalLab/0.6; research; +local)"}
    with httpx.Client(headers=headers, timeout=60, follow_redirects=True,
                      transport=httpx.HTTPTransport(retries=3)) as client:
        versions_body, versions_meta = fetch(client, "ecb_foedb_versions", VERSIONS_URL)
        versions = version_descriptor(versions_body)
        data_root = f"{FOEDB_BASE}/{versions['version']}/{versions['hash']}"
        metadata_body, metadata_meta = fetch(client, "ecb_foedb_metadata", data_root + "/metadata.json")
        metadata = json.loads(metadata_body)
        if metadata.get("name") != "publications.en" or metadata.get("chunk_size") != 250:
            raise ValueError("Unexpected ECB FOEDB metadata")
        snapshots.extend([versions_meta, metadata_meta])
        bodies = []
        max_chunks = math.ceil(metadata["total_records"] / metadata["chunk_size"])
        for chunk_id in range(max_chunks):
            group = chunk_id // metadata["chunk_group_size"]
            body, meta = fetch(client, "ecb_foedb_chunk", f"{data_root}/data/{group}/chunk_{chunk_id}.json")
            records = decode_records(body, metadata)
            snapshots.append(meta); bodies.append(body); chunk_items.append(meta)
            oldest = min(datetime.fromtimestamp(row["pub_timestamp"], UTC).date() for row in records)
            if oldest < start:
                break
        links = decision_links(bodies, metadata, start, end)
        if not links:
            raise ValueError("No ECB policy decisions in requested period")
        for link in links:
            body, meta = fetch(client, "ecb_policy_decision", link["url"])
            normalize_decision(body, link, meta)
            snapshots.append(meta); releases.append({"link": link, "snapshot": meta})
    manifest = {"provider": "ecb_policy_decisions", "requested_start": str(start), "requested_end": str(end),
                "versions": versions_meta, "metadata": metadata_meta, "chunks": chunk_items,
                "releases": releases, "snapshots": snapshots, "fetched_at": datetime.now(UTC).isoformat()}
    atomic_json(root()/"reports"/"ecb_policy_fetch.json", manifest)
    return manifest


def replay_ecb_policy(manifest: Path | dict) -> dict:
    source = json.loads(manifest.read_text(encoding="utf-8")) if isinstance(manifest, Path) else manifest
    for meta in source["snapshots"]:
        body = (root()/meta["payload"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise ValueError("ECB policy snapshot checksum mismatch")
    metadata = json.loads((root()/source["metadata"]["payload"]).read_bytes())
    chunks = [(root()/meta["payload"]).read_bytes() for meta in source["chunks"]]
    expected = decision_links(chunks, metadata, date.fromisoformat(source["requested_start"]), date.fromisoformat(source["requested_end"]))
    if [row["url"] for row in expected] != [item["link"]["url"] for item in source["releases"]]:
        raise ValueError("ECB manifest decisions disagree with preserved FOEDB index")
    rows = [normalize_decision((root()/item["snapshot"]["payload"]).read_bytes(), item["link"], item["snapshot"])
            for item in source["releases"]]
    rows.sort(key=lambda row: row["published_at"])
    if not rows or len({row["event_id"] for row in rows}) != len(rows):
        raise ValueError("ECB policy replay produced no rows or duplicates")
    for previous, current in zip(rows, rows[1:]):
        current["previous_deposit_rate"] = previous["deposit_rate"]
        current["deposit_change_bp"] = round((current["deposit_rate"] - previous["deposit_rate"]) * 100, 8)
    normalized = [{key: value for key, value in row.items() if key != "ingested_at"} for row in rows]
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    signature = {"parser": PARSER, "normalized_sha256": normalized_sha256,
                 "snapshots": [(m["source_url"], m["sha256"]) for m in source["snapshots"]]}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root()/"silver"/"ecb_policy"/dataset_id
    write_macro_parquet(rows, folder/"decisions.parquet",
                        ("event_time", "scheduled_release_at", "published_at", "available_at", "forecast_vintage"))
    report = {"dataset_id": dataset_id, "parser": PARSER, "provider": source["provider"],
              "requested_start": source["requested_start"], "requested_end": source["requested_end"],
              "rows": len(rows), "first_published_at": rows[0]["published_at"].isoformat(),
              "last_published_at": rows[-1]["published_at"].isoformat(),
              "holds": sum(row["deposit_change_bp"] == 0 for row in rows),
              "cuts": sum(row["deposit_change_bp"] is not None and row["deposit_change_bp"] < 0 for row in rows),
              "hikes": sum(row["deposit_change_bp"] is not None and row["deposit_change_bp"] > 0 for row in rows),
              "unknown_availability_rows": sum(row["available_at"] is None for row in rows),
              "normalized_sha256": normalized_sha256,
              "files": {"decisions": str((folder/"decisions.parquet").relative_to(root()))},
              "snapshots": source["snapshots"], "strict_pit_eligible": False,
              "limitations": [
                  "FOEDB supplies the exact 14:15 Frankfurt publication timestamp and official page URL.",
                  "Historical system receipt is not proven, so available_at remains null and strict PIT is disabled.",
                  "Deposit-rate changes are derived only inside the requested window; the first row has no predecessor.",
                  "Consensus, forecast vintage and policy surprise are unavailable and remain null.",
              ], "generated_at": datetime.now(UTC).isoformat()}
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"ecb_policy.json", report)
    return report


def backfill_ecb_policy(start: date, end: date) -> dict:
    return replay_ecb_policy(fetch_ecb_policy_snapshots(start, end))
