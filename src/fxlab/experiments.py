"""Descriptive M5 baselines and an explicit published-effect replication registry."""
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

import duckdb

from .alignment import HORIZONS
from .macro import write_macro_parquet
from .store import atomic_json, root

UTC = timezone.utc
PARSER = "descriptive-event-baseline-1"
MIN_INFERENCE_N = 8
HAC_LAGS = {1: 0, 5: 0, 20: 1, 60: 3}
REPLICATIONS = [
    {
        "id": "macro-surprise-fx-jump",
        "title": "Macro announcement surprise → FX conditional-mean jump",
        "source": "Andersen, Bollerslev, Diebold & Vega (2003)",
        "url": "https://www.nber.org/papers/w8959",
        "required": "Point-in-time expectations, actual releases and intraday FX quotes",
        "status": "unavailable",
        "reason": "Historical consensus is absent; H1 bars cannot reproduce the original high-frequency window.",
    },
    {
        "id": "us-macro-interdealer-fx",
        "title": "US payroll and other macro surprises → immediate interdealer FX response",
        "source": "Federal Reserve IFDP 823 (2004)",
        "url": "https://www.federalreserve.gov/pubs/ifdp/2004/823/ifdp823.htm",
        "required": "Announcement expectations and transaction-level or minute FX data",
        "status": "unavailable",
        "reason": "The MVP has no historical expectation vintage and stores H1 rather than the paper's narrow response window.",
    },
    {
        "id": "fomc-path-surprise-fx",
        "title": "FOMC target/path surprise → dollar exchange-rate response",
        "source": "Federal Reserve IFDP 886 (2006)",
        "url": "https://www.federalreserve.gov/pubs/ifdp/2006/886/ifdp886.htm",
        "required": "Fed funds/eurodollar futures surprises and narrow-window exchange rates",
        "status": "unavailable",
        "reason": "The actual target decision is not a policy surprise; futures-based target/path factors are absent.",
    },
    {
        "id": "ecb-policy-surprises-eurusd",
        "title": "ECB target/communication surprises → EUR/USD response",
        "source": "ECB Working Paper 2281 (2019)",
        "url": "https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp2281~3303fd281b.en.pdf",
        "required": "Intraday OIS factors, press-release and press-conference windows, EUR/USD quotes",
        "status": "unavailable",
        "reason": "The MVP has policy rates but no OIS surprise factors or press-conference event window.",
    },
    {
        "id": "realtime-fundamentals-eurusd",
        "title": "Standardized real-time macro and policy news → USD/EUR returns",
        "source": "ECB Working Paper 365 (2004)",
        "url": "https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp365.pdf",
        "required": "Survey expectations, first-release actuals and standardized surprises",
        "status": "unavailable",
        "reason": "First-release actuals exist for part of the sample, but point-in-time survey expectations do not.",
    },
]


def newey_west_mean(values: list[float], lags: int) -> tuple[float | None, float | None, float | None]:
    """Return HAC standard error, t-statistic and two-sided normal p-value."""
    n = len(values)
    if n < 2 or lags < 0:
        return None, None, None
    center = mean(values)
    deviations = [value - center for value in values]
    long_run = sum(value * value for value in deviations) / n
    for lag in range(1, min(lags, n - 1) + 1):
        covariance = sum(deviations[index] * deviations[index - lag] for index in range(lag, n)) / n
        long_run += 2 * (1 - lag / (lags + 1)) * covariance
    variance = max(0.0, long_run / n)
    standard_error = math.sqrt(variance)
    if standard_error == 0:
        return standard_error, None, None
    statistic = center / standard_error
    return standard_error, statistic, math.erfc(abs(statistic) / math.sqrt(2))


def benjamini_hochberg(rows: list[dict]) -> None:
    tests = sorted([(row["p_value"], index) for index, row in enumerate(rows) if row["p_value"] is not None])
    total = len(tests)
    adjusted = 1.0
    for rank_from_end, (p_value, index) in enumerate(reversed(tests), 1):
        rank = total - rank_from_end + 1
        adjusted = min(adjusted, p_value * total / rank)
        rows[index]["q_value_bh"] = min(1.0, adjusted)


def summarize(group: list[dict], horizon: int) -> dict:
    usable = [row for row in group if row[f"ret_{horizon}d"] is not None]
    values = [row[f"ret_{horizon}d"] for row in usable]
    intervals = sorted((row["target_start_at"], row[f"target_{horizon}d_end"]) for row in usable)
    overlapping, prior_end = 0, None
    for start, end in intervals:
        if prior_end is not None and start < prior_end:
            overlapping += 1
        prior_end = end if prior_end is None else max(prior_end, end)
    result = {
        "source": group[0]["source"], "event_type": group[0]["event_type"], "horizon_sessions": horizon,
        "n": len(values), "overlapping_windows": overlapping,
        "first_prediction_time": usable[0]["prediction_time"] if usable else None,
        "last_prediction_time": usable[-1]["prediction_time"] if usable else None,
        "mean_return": mean(values) if values else None, "median_return": median(values) if values else None,
        "std_return": stdev(values) if len(values) > 1 else None,
        "positive_share": sum(value > 0 for value in values) / len(values) if values else None,
        "zero_mae": mean(abs(value) for value in values) if values else None,
        "zero_rmse": math.sqrt(mean(value * value for value in values)) if values else None,
        "hac_lags": HAC_LAGS[horizon], "hac_se": None, "t_stat": None, "p_value": None,
        "q_value_bh": None, "inference_status": "insufficient_n" if len(values) < MIN_INFERENCE_N else "diagnostic",
        "strict_pit_eligible": False,
    }
    if len(values) >= MIN_INFERENCE_N:
        result["hac_se"], result["t_stat"], result["p_value"] = newey_west_mean(values, HAC_LAGS[horizon])
    return result


def build_baseline_experiments() -> dict:
    alignment_path = root()/"reports"/"event_alignment.json"
    if not alignment_path.exists():
        raise ValueError("Required report missing: event_alignment.json")
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    parquet = root()/alignment["files"]["event_targets"]
    with duckdb.connect() as con:
        con.execute("SET TimeZone='UTC'")
        raw = con.execute(
            "SELECT source, event_type, event_id, prediction_time, target_start_at, "
            "target_1d_end, ret_1d, target_5d_end, ret_5d, target_20d_end, ret_20d, target_60d_end, ret_60d "
            "FROM read_parquet(?) WHERE prediction_mode='pre_event' AND source IN ('bls','fomc','ecb') "
            "ORDER BY source, event_type, prediction_time", [str(parquet)]).fetchall()
    columns = ("source", "event_type", "event_id", "prediction_time", "target_start_at",
               "target_1d_end", "ret_1d", "target_5d_end", "ret_5d", "target_20d_end", "ret_20d",
               "target_60d_end", "ret_60d")
    groups = defaultdict(list)
    for values in raw:
        row = dict(zip(columns, values))
        groups[(row["source"], row["event_type"])].append(row)
    results = []
    for group in groups.values():
        for horizon in HORIZONS:
            results.append(summarize(group, horizon))
    benjamini_hochberg(results)
    normalized_sha256 = hashlib.sha256(json.dumps(results, sort_keys=True, default=str).encode()).hexdigest()
    signature = {"parser": PARSER, "alignment_dataset_id": alignment["dataset_id"],
                 "alignment_sha256": alignment["normalized_sha256"], "normalized_sha256": normalized_sha256,
                 "replication_ids": [item["id"] for item in REPLICATIONS]}
    dataset_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()[:20]
    folder = root()/"gold"/"baseline_experiments"/dataset_id
    write_macro_parquet(results, folder/"results.parquet", ("first_prediction_time", "last_prediction_time"))
    inference_rows = [row for row in results if row["inference_status"] == "diagnostic"]
    report = {
        "dataset_id": dataset_id, "parser": PARSER, "normalized_sha256": normalized_sha256,
        "alignment_dataset_id": alignment["dataset_id"], "input_rows": len(raw),
        "event_groups": len(groups), "result_rows": len(results),
        "diagnostic_inference_rows": len(inference_rows),
        "bh_q_below_005": sum(row["q_value_bh"] is not None and row["q_value_bh"] < .05 for row in results),
        "strict_pit_rows": 0, "replications": REPLICATIONS,
        "replication_status": {"available": 0, "unavailable": len(REPLICATIONS)},
        "files": {"results": str((folder/"results.parquet").relative_to(root()))},
        "method": {
            "sample": "pre_event BLS/FOMC/ECB rows; CFTC schedule events excluded",
            "estimand": "unconditional mean EUR/USD return after an event boundary",
            "hac_lags_by_horizon": {str(key): value for key, value in HAC_LAGS.items()},
            "minimum_n": MIN_INFERENCE_N,
            "multiple_testing": "Benjamini-Hochberg across all diagnostic mean-return tests",
        },
        "limitations": [
            "These are descriptive zero-mean baselines, not published-effect replications and not trading signals.",
            "Actual values are not used as pre-event features; no direction or surprise regression is estimated.",
            "All input rows are non-strict PIT because historical receipt and market vintages are unverified.",
            "HAC lag choices are horizon-based diagnostics; samples are small and longer target windows overlap.",
            "A q-value below a threshold would remain exploratory because hypotheses were not powered or preregistered on this sample.",
        ],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"baseline_experiments.json", report)
    return report
