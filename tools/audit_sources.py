"""Bounded public-data reconnaissance; current values are not training features."""
import csv
import io
import json
import zipfile
from datetime import datetime, timezone

import httpx

from fxlab.store import atomic_json, root, save_raw

results = []


def fetch(source, url):
    with httpx.Client(timeout=50, follow_redirects=True) as client:
        response = client.get(url)
    meta = save_raw("audit_"+source, str(response.url), response.content, dict(response.headers), response.status_code)
    response.raise_for_status()
    return response, meta


def run(name, callback):
    try:
        result = callback()
        result["status"] = "measured"
    except Exception as error:
        result = {"status": "needs_review", "error": str(error)}
    result.update(source=name, checked_at=datetime.now(timezone.utc).isoformat())
    results.append(result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def bls(series):
    url = f"https://api.bls.gov/publicAPI/v2/timeseries/data/{series}?startyear=2017&endyear=2026"
    response, meta = fetch("bls", url)
    data = response.json()
    if data.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError(data.get("message", data.get("status")))
    observations = data["Results"]["series"][0]["data"]
    periods = sorted(row["year"]+"-"+row["period"] for row in observations)
    return {"indicator": series, "rows": len(periods), "first": periods[0], "last": periods[-1],
            "messages": data.get("message"), "raw": meta,
            "pit": "Current series values, not verified historical vintages or release timestamps"}


def eurostat():
    url = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr?geo=EA20&coicop18=TOTAL&unit=RCH_A&sinceTimePeriod=2023-01&lang=EN"
    response, meta = fetch("eurostat", url)
    data = response.json()
    times = data.get("dimension", {}).get("time", {}).get("category", {}).get("index", {})
    if not data.get("value"):
        raise ValueError("Eurostat returned no observations")
    return {"dataset": "prc_hicp_minr", "dimensions": data.get("id"), "shape": data.get("size"),
            "values": len(data.get("value", {})), "first": min(times) if times else None,
            "last": max(times) if times else None, "raw": meta, "pit": "Current ECOICOP2 history; not historical first releases"}


def fred():
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2&cosd=2017-01-01&coed=2026-09-23"
    response, meta = fetch("fred", url)
    rows = list(csv.DictReader(io.StringIO(response.text)))
    if not rows or "DGS2" not in rows[0]:
        raise ValueError("No DGS2 CSV data")
    values = [row for row in rows if row["DGS2"] not in {"", "."}]
    return {"indicator": "DGS2", "rows": len(values), "first": values[0], "last": values[-1],
            "raw": meta, "pit": "Current historical CSV, not ALFRED vintage series; daily timestamps still need verification"}


def cftc(year):
    response, meta = fetch("cftc", f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip")
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        name = next(name for name in archive.namelist() if name.lower().endswith((".txt", ".csv")))
        records = list(csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig"))))
    records = [{key.strip(): value.strip() for key, value in row.items()} for row in records]
    euro = [row for row in records if row.get("CFTC_Contract_Market_Code", "").lstrip("0") == "99741"]
    if not euro:
        raise ValueError("No canonical EUR FX contract 099741 in TFF archive")
    dates = sorted(row["Report_Date_as_YYYY-MM-DD"] for row in euro)
    return {"year": year, "rows_eur": len(euro), "first_report_date": dates[0], "last_report_date": dates[-1],
            "raw": meta, "pit": "Tuesday report date; actual publication calendar not yet recovered"}


if __name__ == "__main__":
    run("bls_cpi", lambda: bls("CUSR0000SA0"))
    run("bls_payrolls", lambda: bls("CES0000000001"))
    run("eurostat_hicp", eurostat)
    run("fred_us2y", fred)
    for year in (2023, 2024, 2025, 2026):
        run("cftc_tff", lambda year=year: cftc(year))
    atomic_json(root()/"reports"/"source_audit.json", results)
