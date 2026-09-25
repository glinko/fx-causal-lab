"""M7 interaction requirements and leakage-aware descriptive regime slices."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

import duckdb

from .alignment import HORIZONS
from .macro import write_macro_parquet
from .store import atomic_json, root

UTC = timezone.utc
PARSER = "descriptive-interaction-regimes-1"
POSITIONING_HISTORY_MIN = 8
POSITIONING_HISTORY_MAX = 52
TREND_LOOKBACK = 20

PLANNED_INTERACTIONS = [
    {
        "id": "surprise-x-positioning",
        "title": "Macro surprise × CFTC positioning → EUR/USD",
        "status": "unavailable",
        "required": "Point-in-time macro consensus plus strict CFTC availability",
        "reason": "Consensus is absent and CFTC availability is conservative or unknown.",
        "graph_edge_id": "positioning_moderates_eurusd",
    },
    {
        "id": "surprise-x-market-regime",
        "title": "Macro surprise × market regime → EUR/USD",
        "status": "unavailable",
        "required": "Point-in-time macro consensus and a preregistered regime definition",
        "reason": "No surprise feature exists; Actual or its sign is not used as a substitute.",
    },
    {
        "id": "oil-x-inflation-expectations",
        "title": "Oil × inflation expectations → rate expectations → EUR/USD",
        "status": "unavailable",
        "required": "Versioned oil, inflation-expectation and rate-expectation series",
        "reason": "None of the three point-in-time feature families is ingested.",
        "graph_edge_id": "oil_us_inflation",
    },
    {
        "id": "rate-differential-x-risk-sentiment",
        "title": "US–EA rate differential × risk sentiment → EUR/USD",
        "status": "unavailable",
        "required": "Comparable US/EA yield vintages and a fixed risk-sentiment measure",
        "reason": "The yield spread and risk-sentiment inputs are unresolved.",
        "graph_edge_id": "spread_eurusd",
    },
]

EXECUTED_SLICES = [
    {
        "id": "event-x-positioning-regime",
        "title": "Event occurrence × expanding CFTC positioning regime",
        "status": "descriptive_non_strict",
        "question": "Do unconditional post-event returns differ across positioning regimes?",
        "not_a_substitute_for": "surprise-x-positioning",
    },
    {
        "id": "event-x-fx-trend-regime",
        "title": "Event occurrence × trailing 20-session EUR/USD trend",
        "status": "descriptive_non_strict",
        "question": "Do unconditional post-event returns differ after uptrends versus downtrends?",
        "not_a_substitute_for": "surprise-x-market-regime",
    },
]


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Interaction timestamps must be timezone-aware")
    return value.astimezone(UTC)


def positioning_snapshot(prediction_time: datetime, positions: list[dict]) -> tuple[dict | None, str | None]:
    prediction_time = _aware(prediction_time)
    eligible = [row for row in positions if _aware(row.get("available_at")) is not None
                and _aware(row["available_at"]) <= prediction_time]
    eligible.sort(key=lambda row: _aware(row["available_at"]))
    if not eligible:
        return None, "no_available_position"
    if len(eligible) < POSITIONING_HISTORY_MIN:
        return None, "insufficient_position_history"
    history = eligible[-POSITIONING_HISTORY_MAX:]
    latest = history[-1]
    value = latest["leveraged_funds_net_share_oi"]
    values = [row["leveraged_funds_net_share_oi"] for row in history]
    less = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    percentile = (less + 0.5 * equal) / len(values)
    regime = "short_crowded" if percentile <= 1/3 else "long_crowded" if percentile >= 2/3 else "neutral"
    return {
        "feature_name": "positioning_regime",
        "regime": regime,
        "feature_value": value,
        "feature_percentile": percentile,
        "feature_available_at": _aware(latest["available_at"]),
        "feature_observation_id": latest["observation_id"],
        "feature_history_count": len(history),
        "feature_time_quality": latest["time_quality"],
    }, None


def trend_snapshot(prediction_time: datetime, daily: list[dict]) -> tuple[dict | None, str | None]:
    prediction_time = _aware(prediction_time)
    eligible = [row for row in daily if row.get("complete") and _aware(row.get("available_at")) is not None
                and _aware(row["available_at"]) <= prediction_time]
    eligible.sort(key=lambda row: _aware(row["available_at"]))
    if len(eligible) < TREND_LOOKBACK + 1:
        return None, "insufficient_trend_history"
    history = eligible[-(TREND_LOOKBACK + 1):]
    trailing_return = history[-1]["close"] / history[0]["close"] - 1
    return {
        "feature_name": "fx_trend_20d",
        "regime": "eur_uptrend" if trailing_return > 0 else "eur_downtrend",
        "feature_value": trailing_return,
        "feature_percentile": None,
        "feature_available_at": _aware(history[-1]["available_at"]),
        "feature_observation_id": str(history[-1]["session_date"]),
        "feature_history_count": len(history),
        "feature_time_quality": history[-1]["time_quality"],
    }, None


def validate_feature_row(row: dict) -> None:
    if _aware(row["feature_available_at"]) > _aware(row["prediction_time"]):
        raise ValueError("Interaction feature becomes available after prediction_time")
    if _aware(row["target_start_at"]) < _aware(row["prediction_time"]):
        raise ValueError("Interaction target starts before prediction_time")
    if "actual" in row or "surprise" in row:
        raise ValueError("M7 descriptive regimes cannot use Actual or surprise")
    if row["strict_pit_eligible"]:
        raise ValueError("Current interaction inputs cannot be strict PIT")


def _overlap(rows: list[dict], horizon: int) -> int:
    intervals = sorted((row["target_start_at"], row[f"target_{horizon}d_end"])
                       for row in rows if row[f"target_{horizon}d_end"] is not None)
    count, prior_end = 0, None
    for start, end in intervals:
        if prior_end is not None and start < prior_end:
            count += 1
        prior_end = end if prior_end is None else max(prior_end, end)
    return count


def summarize_regime(rows: list[dict], horizon: int) -> dict | None:
    usable = [row for row in rows if row[f"ret_{horizon}d"] is not None]
    if not usable:
        return None
    values = [row[f"ret_{horizon}d"] for row in usable]
    sample_warning = "single_observation" if len(values) == 1 else "small_sample" if len(values) < 8 else None
    first = rows[0]
    return {
        "feature_name": first["feature_name"],
        "source": first["source"],
        "event_type": first["event_type"],
        "regime": first["regime"],
        "horizon_sessions": horizon,
        "n": len(values),
        "mean_return": mean(values),
        "median_return": median(values),
        "positive_share": sum(value > 0 for value in values) / len(values),
        "overlapping_windows": _overlap(usable, horizon),
        "inference_status": "descriptive_non_strict",
        "sample_warning": sample_warning,
        "strict_pit_eligible": False,
    }


def _read(path: Path, query: str) -> list[tuple]:
    with duckdb.connect() as connection:
        connection.execute("SET TimeZone='UTC'")
        return connection.execute(query, [str(path)]).fetchall()


def build_interaction_experiments() -> dict:
    report_names = {"alignment": "event_alignment.json", "cftc": "cftc.json", "bars": "bars.json"}
    reports = {}
    for key, name in report_names.items():
        path = root()/"reports"/name
        if not path.exists():
            raise ValueError(f"Required report missing: {name}")
        reports[key] = json.loads(path.read_text(encoding="utf-8"))

    alignment = reports["alignment"]
    event_path = root()/alignment["files"]["event_targets"]
    event_columns = ("alignment_id", "event_id", "source", "event_type", "prediction_time", "target_start_at",
                     "target_1d_end", "ret_1d", "target_5d_end", "ret_5d",
                     "target_20d_end", "ret_20d", "target_60d_end", "ret_60d")
    event_rows = [dict(zip(event_columns, row)) for row in _read(
        event_path,
        "SELECT alignment_id,event_id,source,event_type,prediction_time,target_start_at,"
        "target_1d_end,ret_1d,target_5d_end,ret_5d,target_20d_end,ret_20d,target_60d_end,ret_60d "
        "FROM read_parquet(?) WHERE prediction_mode='pre_event' AND source IN ('bls','fomc','ecb') "
        "ORDER BY prediction_time,source,event_type",
    )]

    cftc = reports["cftc"]
    position_columns = ("observation_id", "report_date", "available_at", "time_quality", "leveraged_funds_net_share_oi")
    positions = [dict(zip(position_columns, row)) for row in _read(
        root()/cftc["files"]["positions"],
        "SELECT observation_id,report_date,available_at,time_quality,leveraged_funds_net_share_oi "
        "FROM read_parquet(?) ORDER BY available_at NULLS LAST,report_date",
    )]

    bars = reports["bars"]
    daily_columns = ("session_date", "close", "available_at", "complete", "time_quality")
    daily = [dict(zip(daily_columns, row)) for row in _read(
        root()/bars["files"]["d1"],
        "SELECT session_date,close,available_at,complete,time_quality FROM read_parquet(?) ORDER BY available_at",
    )]

    feature_rows, exclusions = [], Counter()
    builders = (("positioning_regime", lambda when: positioning_snapshot(when, positions)),
                ("fx_trend_20d", lambda when: trend_snapshot(when, daily)))
    for event in event_rows:
        for feature_name, builder in builders:
            feature, reason = builder(event["prediction_time"])
            if feature is None:
                exclusions[f"{feature_name}:{reason}"] += 1
                continue
            row = {**event, **feature,
                   "interaction_id": f'{event["alignment_id"]}|{feature_name}',
                   "strict_pit_eligible": False}
            validate_feature_row(row)
            feature_rows.append(row)
    feature_rows.sort(key=lambda row: (row["prediction_time"], row["interaction_id"]))
    if not feature_rows or len({row["interaction_id"] for row in feature_rows}) != len(feature_rows):
        raise ValueError("Interaction build produced no feature rows or duplicate IDs")

    groups = defaultdict(list)
    for row in feature_rows:
        groups[(row["feature_name"], row["source"], row["event_type"], row["regime"])].append(row)
    results = []
    for group in groups.values():
        for horizon in HORIZONS:
            summary = summarize_regime(group, horizon)
            if summary:
                results.append(summary)
    results.sort(key=lambda row: (row["feature_name"], row["source"], row["event_type"], row["regime"],
                                  row["horizon_sessions"]))

    normalized = {"features": feature_rows, "results": results, "planned": PLANNED_INTERACTIONS}
    normalized_sha256 = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    inputs = {key: {"dataset_id": value.get("dataset_id"), "normalized_sha256": value.get("normalized_sha256")}
              for key, value in reports.items()}
    signature = {"parser": PARSER, "inputs": inputs, "normalized_sha256": normalized_sha256,
                 "positioning_history_min": POSITIONING_HISTORY_MIN, "trend_lookback": TREND_LOOKBACK}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root()/"gold"/"interaction_experiments"/dataset_id
    timestamps = ("prediction_time", "target_start_at", "feature_available_at",
                  *(f"target_{horizon}d_end" for horizon in HORIZONS))
    write_macro_parquet(feature_rows, folder/"features.parquet", timestamps)
    write_macro_parquet(results, folder/"results.parquet", ())

    feature_counts = Counter(row["feature_name"] for row in feature_rows)
    regime_counts = Counter(f'{row["feature_name"]}:{row["regime"]}' for row in feature_rows)
    result_warnings = Counter(row["sample_warning"] or "none" for row in results)
    report = {
        "dataset_id": dataset_id,
        "parser": PARSER,
        "normalized_sha256": normalized_sha256,
        "inputs": inputs,
        "input_event_rows": len(event_rows),
        "feature_rows": len(feature_rows),
        "feature_counts": dict(sorted(feature_counts.items())),
        "regime_counts": dict(sorted(regime_counts.items())),
        "regime_groups": len(groups),
        "result_rows": len(results),
        "result_warnings": dict(sorted(result_warnings.items())),
        "strict_pit_rows": 0,
        "exclusions": dict(sorted(exclusions.items())),
        "executed_slices": EXECUTED_SLICES,
        "planned_interactions": PLANNED_INTERACTIONS,
        "files": {
            "features": (folder/"features.parquet").relative_to(root()).as_posix(),
            "results": (folder/"results.parquet").relative_to(root()).as_posix(),
        },
        "method": {
            "sample": "pre_event BLS/FOMC/ECB rows only; Actual and surprise are not selected",
            "positioning": "latest CFTC leveraged-funds net share available by prediction_time; expanding rank over 8–52 available reports",
            "positioning_regimes": "short_crowded <= 1/3; neutral; long_crowded >= 2/3",
            "fx_trend": "sign of trailing 20 complete NY17 session return, using only D1 bars available by prediction_time",
            "inference": "descriptive only; no p-values, q-values, causal effect or trading rule",
        },
        "limitations": [
            "Executed slices condition event occurrence on regimes; they do not replace the unavailable surprise interactions.",
            "CFTC availability and market-history vintages are non-strict, so every row remains ineligible for strict PIT inference.",
            "Samples are small, target windows overlap and no formal regime contrast is estimated.",
            "Actual values, their signs and post-release reactions are not used as pre-event features.",
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"interaction_experiments.json", report)
    return report
