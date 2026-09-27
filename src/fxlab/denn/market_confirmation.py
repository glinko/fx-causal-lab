"""Confirm registered daily relationships on the long Dukascopy EUR/USD series."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median

import duckdb
import yaml

from ..store import atomic_json, root
from .pipeline import _write_parquet
from .spectral import correlation

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "market_confirmation.yaml"


def _load_config(path: Path) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-market-confirmation-1":
        raise ValueError("Unsupported market confirmation config")
    lags = config.get("registered_lags")
    if not isinstance(lags, list) or not lags or min(lags) < 0 or len(lags) != len(set(lags)):
        raise ValueError("Registered lags must be unique non-negative integers")
    if config["rolling_window_sessions"] < 100 or config["rolling_step_sessions"] < 1:
        raise ValueError("Invalid rolling window settings")
    primary = config["primary_check"]
    if primary["factor"] not in config["series"] or primary["lag_sessions"] not in lags:
        raise ValueError("Primary check must be one of the registered factor/lag pairs")
    if primary["expected_sign"] not in {"negative", "positive"}:
        raise ValueError("Primary sign must be negative or positive")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _transform(values: list[float], method: str, scale: float | None = None) -> list[float]:
    result = []
    for previous, current in zip(values, values[1:]):
        if method == "difference":
            result.append(current - previous)
        elif method == "log_difference":
            if previous <= 0 or current <= 0:
                raise ValueError("Log difference requires positive values")
            result.append(math.log(current / previous))
        elif method == "asinh_difference":
            if scale is None or scale <= 0:
                raise ValueError("asinh difference requires a positive scale")
            result.append(math.asinh(current / scale) - math.asinh(previous / scale))
        else:
            raise ValueError(f"Unsupported transform: {method}")
    return result


def _rolling_windows(size: int, length: int, step: int) -> list[tuple[int, int]]:
    if size < length:
        raise ValueError("Insufficient observations for rolling confirmation")
    starts = list(range(0, size - length + 1, step))
    if starts[-1] != size - length:
        starts.append(size - length)
    return [(start, start + length) for start in starts]


def _lagged(left: list[float], right: list[float], lag: int) -> tuple[list[float], list[float]]:
    return (left[:-lag], right[lag:]) if lag else (left, right)


def _load_inputs() -> tuple[list, dict[str, list[float]], dict]:
    report_path = root() / "reports" / "data_coverage.json"
    if not report_path.exists():
        raise ValueError("Data coverage report is required")
    coverage = json.loads(report_path.read_text(encoding="utf-8"))
    if "common_market_d1" not in coverage.get("files", {}):
        raise ValueError("Long market EUR/USD common grid is required")
    market_path = root() / coverage["files"]["common_market_d1"]
    reference_path = root() / coverage["files"]["common_d1"]
    with duckdb.connect() as connection:
        rows = connection.execute(
            'SELECT m.observation_date,m."EURUSD",r."EURUSD_REF",m."US_2Y",m."EA_2Y",'
            'm."US_10Y",m."EA_10Y",m."BRENT",m."WTI",m."VIX" '
            'FROM read_parquet(?) m JOIN read_parquet(?) r USING (observation_date) '
            'ORDER BY m.observation_date',
            [str(market_path), str(reference_path)],
        ).fetchall()
    if len(rows) < 1025:
        raise ValueError("Market/reference overlap is too short")
    values = {
        "EURUSD": [float(row[1]) for row in rows],
        "EURUSD_REF": [float(row[2]) for row in rows],
        "US_EA_2Y": [float(row[3] - row[4]) for row in rows],
        "US_EA_10Y": [float(row[5] - row[6]) for row in rows],
        "BRENT": [float(row[7]) for row in rows],
        "WTI": [float(row[8]) for row in rows],
        "VIX": [float(row[9]) for row in rows],
    }
    return [row[0] for row in rows], values, coverage


def build_market_confirmation(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    dates, raw, coverage = _load_inputs()
    targets = {name: _transform(raw[source], "log_difference") for name, source in config["targets"].items()}
    factors = {
        name: _transform(raw[details["source"]], details["transform"], details.get("scale"))
        for name, details in config["series"].items()
    }
    if len({len(values) for values in [*targets.values(), *factors.values()]}) != 1:
        raise ValueError("Transformed market confirmation series are misaligned")

    target_correlation = correlation(targets["reference"], targets["market"])
    pairs = [(left, right) for left, right in zip(targets["reference"], targets["market"])
             if left != 0 and right != 0]
    direction_agreement = sum((left > 0) == (right > 0) for left, right in pairs) / len(pairs)
    mean_absolute_difference_bps = fmean(abs(left - right) for left, right in zip(
        targets["reference"], targets["market"])) * 10_000

    full_rows, rolling_rows = [], []
    windows = _rolling_windows(len(dates) - 1, config["rolling_window_sessions"], config["rolling_step_sessions"])
    for target_name, target in targets.items():
        for factor_name, factor in factors.items():
            for lag in config["registered_lags"]:
                left, right = _lagged(factor, target, lag)
                value = correlation(left, right)
                if value is None:
                    raise ValueError("Undefined full-sample correlation")
                full_rows.append({"target": target_name, "factor": factor_name, "lag_sessions": lag,
                                  "correlation": value, "observations": len(left)})
                for number, (start, end) in enumerate(windows, 1):
                    window_left, window_right = _lagged(factor[start:end], target[start:end], lag)
                    window_value = correlation(window_left, window_right)
                    if window_value is None:
                        raise ValueError("Undefined rolling correlation")
                    rolling_rows.append({
                        "target": target_name, "factor": factor_name, "lag_sessions": lag,
                        "window_number": number, "window_start": dates[start + 1], "window_end": dates[end],
                        "correlation": window_value, "observations": len(window_left),
                    })

    summaries = []
    for target_name in targets:
        for factor_name in factors:
            for lag in config["registered_lags"]:
                selected = [row["correlation"] for row in rolling_rows
                            if row["target"] == target_name and row["factor"] == factor_name
                            and row["lag_sessions"] == lag]
                summaries.append({
                    "target": target_name, "factor": factor_name, "lag_sessions": lag,
                    "rolling_windows": len(selected), "median_correlation": median(selected),
                    "negative_share": sum(value < 0 for value in selected) / len(selected),
                    "positive_share": sum(value > 0 for value in selected) / len(selected),
                    "minimum_correlation": min(selected), "maximum_correlation": max(selected),
                })

    primary = config["primary_check"]
    primary_full = next(row for row in full_rows if row["target"] == "market"
                        and row["factor"] == primary["factor"] and row["lag_sessions"] == primary["lag_sessions"])
    primary_summary = next(row for row in summaries if row["target"] == "market"
                           and row["factor"] == primary["factor"] and row["lag_sessions"] == primary["lag_sessions"])
    expected_negative = primary["expected_sign"] == "negative"
    sign_ok = primary_full["correlation"] < 0 if expected_negative else primary_full["correlation"] > 0
    sign_share = primary_summary["negative_share"] if expected_negative else primary_summary["positive_share"]
    confirmed = sign_ok and abs(primary_full["correlation"]) >= primary["minimum_absolute_correlation"] \
        and sign_share >= primary["minimum_rolling_sign_share"]

    normalized = {
        "input_dataset_id": coverage["dataset_id"], "input_sha256": coverage["normalized_sha256"],
        "config_sha256": config_hash, "target_comparison": {
            "correlation": target_correlation, "direction_agreement": direction_agreement,
            "mean_absolute_difference_bps": mean_absolute_difference_bps,
        }, "full": full_rows, "rolling": rolling_rows, "summaries": summaries,
    }
    normalized_hash = hashlib.sha256(json.dumps(
        normalized, sort_keys=True, default=str, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    folder = root() / "gold" / "denn_market_confirmation" / dataset_id
    files = {
        "full": (folder / "full_correlations.parquet").relative_to(root()).as_posix(),
        "rolling": (folder / "rolling_correlations.parquet").relative_to(root()).as_posix(),
        "summaries": (folder / "rolling_summary.parquet").relative_to(root()).as_posix(),
    }
    _write_parquet(full_rows, root() / files["full"], "target,factor,lag_sessions")
    _write_parquet(rolling_rows, root() / files["rolling"], "target,factor,lag_sessions,window_number")
    _write_parquet(summaries, root() / files["summaries"], "target,factor,lag_sessions")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "normalized_sha256": normalized_hash,
        "generated_at": datetime.now(UTC).isoformat(), "input_dataset_id": coverage["dataset_id"],
        "input_sha256": coverage["normalized_sha256"], "config_sha256": config_hash,
        "date_from": str(dates[1]), "date_to": str(dates[-1]), "observations": len(dates) - 1,
        "rolling_windows": len(windows), "registered_lags": config["registered_lags"],
        "target_comparison": normalized["target_comparison"], "full_correlations": full_rows,
        "rolling_summaries": summaries, "primary_check": {
            **primary, "market_correlation": primary_full["correlation"], "rolling_sign_share": sign_share,
            "confirmed": confirmed, "verdict": "CONFIRMED" if confirmed else "NOT_CONFIRMED",
        },
        "files": files, "strict_pit_eligible": False, "inference_status": "descriptive_non_strict",
        "limitations": [
            "Dukascopy and ECB observations use different daily fixing boundaries; the comparison measures robustness, not identical prices.",
            "Inputs are current-history downloads and are not strict historical vintages.",
            "Rolling windows overlap, so their sign share is a stability description rather than an independent probability.",
            "The registered correlations do not establish cause or a tradable strategy.",
        ],
    }
    atomic_json(root() / "reports" / "denn_market_confirmation.json", report)
    return report
