import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .store import atomic_json, root, save_raw

CONFIG = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "sources.json"


def probe(source):
    result = dict(source)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with httpx.Client(timeout=25, follow_redirects=True) as client:
            response = client.get(source["url"])
        meta = save_raw("recon_" + source["id"], str(response.url), response.content, dict(response.headers), response.status_code)
        result.update(http_status=response.status_code, content_type=response.headers.get("content-type", ""),
                      bytes=len(response.content), raw_sha256=meta["sha256"], final_url=str(response.url))
        result["status"] = "http_ok_unvalidated" if response.is_success else "http_error"
        if source["probe_kind"] == "auth_check":
            result["status"] = "auth_or_contract_unverified"
        if source["probe_kind"] == "data" and response.is_success:
            valid = False
            if source["id"] == "ecb":
                from .providers import normalize_ecb
                from datetime import date
                valid = bool(normalize_ecb(response.content, date(2023, 9, 23), date(2026, 9, 23)))
            elif source["id"] == "bls":
                data = response.json()
                valid = data.get("status") == "REQUEST_SUCCEEDED" and bool(data.get("Results", {}).get("series", [{}])[0].get("data"))
            elif source["id"] == "eurostat":
                valid = bool(response.json().get("value"))
            elif source["id"] == "fred":
                import csv
                import io
                rows = list(csv.DictReader(io.StringIO(response.text)))
                valid = any(row.get("DGS2") not in {None, "", "."} for row in rows)
            elif source["id"] == "cftc":
                import io
                import zipfile
                valid = zipfile.is_zipfile(io.BytesIO(response.content))
            result["status"] = "sample_validated" if valid else "payload_unvalidated"
    except Exception as exc:
        result.update(status="probe_failed", error=str(exc)[:400])
    return result


def run_recon():
    sources = json.loads(CONFIG.read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(probe, sources))
    atomic_json(root() / "reports" / "sources.json", results)
    return results
