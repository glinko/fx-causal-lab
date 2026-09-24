"""Leakage-aware event alignment and forward EUR/USD targets."""
import hashlib
import json
import math
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from .macro import write_macro_parquet
from .store import atomic_json, root

UTC = timezone.utc
HORIZONS = (1, 5, 20, 60)
PARSER = "event-target-alignment-1"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Alignment timestamps must be timezone-aware")
    return value.astimezone(UTC)


def prediction_modes(event: dict) -> list[dict]:
    """Create only modes whose prediction boundary is actually known."""
    reference = _aware(event.get("reference_at"))
    available = _aware(event.get("available_at"))
    modes = []
    if reference is not None:
        modes.append({"prediction_mode": "pre_event", "prediction_time": reference,
                      "reaction_end": None, "actual_feature_eligible": False})
    if available is not None:
        modes.append({"prediction_mode": "post_release", "prediction_time": available,
                      "reaction_end": None, "actual_feature_eligible": True})
        modes.append({"prediction_mode": "reaction_confirmed", "prediction_time": available + timedelta(hours=1),
                      "reaction_end": available + timedelta(hours=1), "actual_feature_eligible": True})
    return modes


def align_event(event: dict, mode: dict, hourly: list[dict], daily: list[dict]) -> dict | None:
    """Anchor at the first scheduled H1 open at or after prediction_time."""
    prediction_time = _aware(mode["prediction_time"])
    starts = [row["bar_start"] for row in hourly]
    index = bisect_left(starts, prediction_time)
    if index >= len(hourly):
        return None
    start = hourly[index]
    if start["bar_start"] - prediction_time > timedelta(days=4):
        return None
    sessions = [row for row in daily if row["bar_end"] > start["bar_start"]]
    result = {
        "alignment_id": f'{event["event_id"]}|{mode["prediction_mode"]}',
        "event_id": event["event_id"], "source": event["source"], "event_type": event["event_type"],
        "observation_period": event.get("observation_period"), "actual": event.get("actual"),
        "actual_unit": event.get("actual_unit"), "reference_time_kind": event["reference_time_kind"],
        "event_published_at": _aware(event.get("published_at")),
        "event_available_at": _aware(event.get("available_at")),
        "event_time_quality": event["time_quality"], "prediction_mode": mode["prediction_mode"],
        "prediction_time": prediction_time, "reaction_end": _aware(mode.get("reaction_end")),
        "actual_feature_eligible": mode["actual_feature_eligible"],
        "target_start_at": start["bar_start"], "target_start_price": start["open"],
        "target_start_bar_end": start["bar_end"],
        "target_start_market_available_at": start["available_at"],
        "price_side": "bid", "target_calendar": "NY17", "return_definition": "close/open - 1",
        "market_history_vintage_verified": False, "strict_pit_eligible": False,
    }
    path_complete = True
    for horizon in HORIZONS:
        prefix = f"target_{horizon}d"
        if len(sessions) < horizon:
            endpoint = None
            reason = "insufficient_future_sessions"
        else:
            path_complete = path_complete and all(row["complete"] for row in sessions[:horizon])
            endpoint = sessions[horizon - 1] if path_complete else None
            reason = None if endpoint else "incomplete_market_path"
        if endpoint:
            simple_return = endpoint["close"] / start["open"] - 1
            result.update({f"{prefix}_end": endpoint["bar_end"], f"{prefix}_close": endpoint["close"],
                           f"ret_{horizon}d": simple_return,
                           f"log_ret_{horizon}d": math.log(endpoint["close"] / start["open"]),
                           f"{prefix}_status": "complete"})
        else:
            result.update({f"{prefix}_end": None, f"{prefix}_close": None, f"ret_{horizon}d": None,
                           f"log_ret_{horizon}d": None, f"{prefix}_status": reason})
    validate_alignment(result)
    return result


def validate_alignment(row: dict) -> None:
    if row["target_start_at"] < row["prediction_time"]:
        raise ValueError("Target starts before prediction_time")
    if row["prediction_mode"] == "pre_event" and row["actual_feature_eligible"]:
        raise ValueError("Pre-event row cannot use the released actual")
    if row["actual_feature_eligible"]:
        available = row["event_available_at"]
        if available is None or available > row["prediction_time"]:
            raise ValueError("Actual used before event availability")
    reaction_end = row.get("reaction_end")
    if reaction_end is not None and reaction_end > row["prediction_time"]:
        raise ValueError("Reaction window extends beyond prediction_time")
    for horizon in HORIZONS:
        endpoint = row[f"target_{horizon}d_end"]
        if endpoint is not None and endpoint < row["target_start_at"]:
            raise ValueError("Target endpoint precedes target start")


def _read(path: Path, query: str) -> list[tuple]:
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        return con.execute(query, [str(path)]).fetchall()


def load_events(reports: dict[str, dict]) -> tuple[list[dict], dict]:
    events = []
    macro = reports["macro"]
    for event_id, indicator, period, actual, unit, published, available, quality in _read(
        root()/macro["files"]["observations"],
        "SELECT observation_id, indicator, observation_period, actual, unit, published_at, available_at, time_quality "
        "FROM read_parquet(?) ORDER BY published_at, indicator",
    ):
        events.append({"event_id": event_id, "source": "bls", "event_type": indicator,
                       "observation_period": period, "actual": actual, "actual_unit": unit,
                       "reference_at": published, "reference_time_kind": "published_at",
                       "published_at": published, "available_at": available, "time_quality": quality})
    fomc = reports["fomc"]
    for event_id, day, published, available, quality, change in _read(
        root()/fomc["files"]["statements"],
        "SELECT event_id, decision_date, published_at, available_at, time_quality, rate_change_bp "
        "FROM read_parquet(?) ORDER BY published_at",
    ):
        events.append({"event_id": event_id, "source": "fomc", "event_type": "fomc_rate_decision",
                       "observation_period": str(day), "actual": change, "actual_unit": "basis_points",
                       "reference_at": published, "reference_time_kind": "published_at",
                       "published_at": published, "available_at": available, "time_quality": quality})
    ecb = reports["ecb"]
    for event_id, day, published, available, quality, change in _read(
        root()/ecb["files"]["decisions"],
        "SELECT event_id, decision_date, published_at, available_at, time_quality, deposit_change_bp "
        "FROM read_parquet(?) ORDER BY published_at",
    ):
        events.append({"event_id": event_id, "source": "ecb", "event_type": "ecb_rate_decision",
                       "observation_period": str(day), "actual": change, "actual_unit": "basis_points",
                       "reference_at": published, "reference_time_kind": "published_at",
                       "published_at": published, "available_at": available, "time_quality": quality})
    cftc = reports["cftc"]
    missing_cftc_reference = 0
    for event_id, day, scheduled, published, available, quality in _read(
        root()/cftc["files"]["positions"],
        "SELECT observation_id, report_date, scheduled_release_at, published_at, available_at, time_quality "
        "FROM read_parquet(?) ORDER BY report_date",
    ):
        if scheduled is None:
            missing_cftc_reference += 1
            continue
        events.append({"event_id": event_id, "source": "cftc", "event_type": "cftc_positioning_release",
                       "observation_period": str(day), "actual": None, "actual_unit": None,
                       "reference_at": scheduled, "reference_time_kind": "scheduled_release_at",
                       "published_at": published, "available_at": available, "time_quality": quality})
    return events, {"cftc_rows_without_release_reference": missing_cftc_reference}


def build_event_targets() -> dict:
    report_paths = {"bars": "bars.json", "macro": "macro_releases.json", "fomc": "fomc.json",
                    "ecb": "ecb_policy.json", "cftc": "cftc.json"}
    reports = {}
    for key, name in report_paths.items():
        path = root()/"reports"/name
        if not path.exists():
            raise ValueError(f"Required report missing: {name}")
        reports[key] = json.loads(path.read_text(encoding="utf-8"))
    bars = reports["bars"]
    hourly = [{"bar_start": start, "bar_end": end, "open": open_price, "available_at": available}
              for start, end, open_price, available in _read(
                  root()/bars["files"]["h1"],
                  "SELECT bar_start, bar_end, open, available_at FROM read_parquet(?) "
                  "WHERE within_schedule ORDER BY bar_start")]
    daily = [{"bar_end": end, "close": close, "complete": complete}
             for end, close, complete in _read(root()/bars["files"]["d1"],
                                               "SELECT bar_end, close, complete FROM read_parquet(?) ORDER BY bar_end")]
    events, exclusions = load_events(reports)
    aligned, no_market_anchor = [], 0
    for event in events:
        for mode in prediction_modes(event):
            row = align_event(event, mode, hourly, daily)
            if row is None:
                no_market_anchor += 1
            else:
                aligned.append(row)
    aligned.sort(key=lambda row: (row["prediction_time"], row["event_id"], row["prediction_mode"]))
    if not aligned or len({row["alignment_id"] for row in aligned}) != len(aligned):
        raise ValueError("Alignment produced no rows or duplicate IDs")
    normalized = [{key: value for key, value in row.items()} for row in aligned]
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    inputs = {key: {"dataset_id": report.get("dataset_id"), "normalized_sha256": report.get("normalized_sha256")}
              for key, report in reports.items()}
    signature = {"parser": PARSER, "inputs": inputs, "normalized_sha256": normalized_sha256,
                 "horizons": HORIZONS}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True, default=str).encode()).hexdigest()[:20]
    folder = root()/"gold"/"event_targets"/dataset_id
    timestamps = ("event_published_at", "event_available_at", "prediction_time", "reaction_end",
                  "target_start_at", "target_start_bar_end", "target_start_market_available_at",
                  *(f"target_{horizon}d_end" for horizon in HORIZONS))
    write_macro_parquet(aligned, folder/"event_targets.parquet", timestamps)
    report = {
        "dataset_id": dataset_id, "parser": PARSER, "normalized_sha256": normalized_sha256,
        "rows": len(aligned), "input_events": len(events),
        "sources": {source: sum(row["source"] == source for row in aligned) for source in ("bls", "fomc", "ecb", "cftc")},
        "prediction_modes": {mode: sum(row["prediction_mode"] == mode for row in aligned)
                             for mode in ("pre_event", "post_release", "reaction_confirmed")},
        "target_coverage": {f"{horizon}d": sum(row[f"ret_{horizon}d"] is not None for row in aligned)
                            for horizon in HORIZONS},
        "strict_pit_rows": sum(row["strict_pit_eligible"] for row in aligned),
        "exclusions": {**exclusions, "rows_without_market_anchor": no_market_anchor},
        "inputs": inputs, "files": {"event_targets": str((folder/"event_targets.parquet").relative_to(root()))},
        "limitations": [
            "Targets use the first H1 open at or after prediction_time and NY17 session closes at 1/5/20/60 trading-session horizons.",
            "An incomplete D1 session invalidates that horizon and every longer horizon; it is never skipped.",
            "BLS, FOMC and ECB have official published_at but unknown historical available_at, so only pre-event rows are emitted.",
            "CFTC uses scheduled_release_at as the pre-event reference; post-release and reaction-confirmed rows use conservative available_at and remain non-strict.",
            "Current broker history is not a verified historical vintage; every row remains strict_pit_eligible=false.",
            "Event actuals are metadata in pre-event rows and are explicitly forbidden as model features by actual_feature_eligible=false.",
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"event_alignment.json", report)
    return report
