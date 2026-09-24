from datetime import datetime, time, timezone
from statistics import median
from zoneinfo import ZoneInfo

import duckdb

from .store import atomic_json, root


def ecb_comparison(bars_report):
    """Sanity comparison around ECB concertation, never an executable-price match."""
    reference = root() / "silver" / "ecb_eurusd.parquet"
    if not reference.exists():
        return {"status": "unavailable", "reason": "ECB reference dataset not loaded"}
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        refs = con.execute("SELECT date, value, raw_payload_hash FROM read_parquet(?) ORDER BY date", [str(reference)]).fetchall()
        candles = con.execute("SELECT bar_start, low, high, close FROM read_parquet(?)", [str(root()/bars_report["files"]["h1"])]).fetchall()
    by_time = {row[0]: row[1:] for row in candles}
    matches, missing = [], 0
    for day, rate, reference_hash in refs:
        # ECB procedure usually around 14:10 Frankfurt local time; H1 containing that instant.
        comparison_time = datetime.combine(day, time(14), ZoneInfo("Europe/Berlin")).astimezone(timezone.utc)
        if comparison_time not in by_time:
            missing += 1
            continue
        low, high, close = by_time[comparison_time]
        matches.append({"date": str(day), "ecb_reference": rate, "hour_start": comparison_time.isoformat(),
                        "h1_low": low, "h1_high": high, "h1_close": close,
                        "distance_to_h1_range_pips": max(low-rate, rate-high, 0)*10000,
                        "distance_to_h1_close_pips": abs(close-rate)*10000, "ecb_raw_hash": reference_hash})
    if not matches:
        return {"status": "unavailable", "reason": "No matching hours"}
    distances = sorted(row["distance_to_h1_range_pips"] for row in matches)
    report = {"status": "completed", "dataset_id": bars_report["dataset_id"], "matched_days": len(matches), "unmatched_ecb_days": missing,
              "median_distance_to_hour_range_pips": median(distances),
              "p95_distance_to_hour_range_pips": distances[int(.95*(len(distances)-1))],
              "days_more_than_10_pips_outside_hour_range": sum(d>10 for d in distances),
              "note": "Sanity check, not exact price validation. ECB fixing and broker bid have different methodologies. Hour chosen around 14:10 Europe/Berlin; timestamps and spreads may differ.",
              "largest_differences": sorted(matches, key=lambda row: row["distance_to_h1_range_pips"], reverse=True)[:10]}
    atomic_json(root()/"reports"/"ecb_comparison.json", report)
    return report
