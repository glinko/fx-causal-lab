"""Chronological stability diagnostics for the deterministic spectral baseline."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean, median

import duckdb
import yaml

from ..store import atomic_json, root
from .pipeline import _write_parquet
from .spectral import correlation, welch_spectra

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "spectral_stability.yaml"
SPECTRAL_CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "spectral.yaml"


def _load_config(path: Path) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-spectral-stability-1":
        raise ValueError("Unsupported spectral stability config version")
    positive = (
        "rolling_window_sessions", "rolling_step_sessions",
        "expanding_minimum_sessions", "expanding_step_sessions",
    )
    if any(not isinstance(config.get(name), int) or config[name] < 1 for name in positive):
        raise ValueError("Spectral stability windows and steps must be positive integers")
    lags = config.get("registered_lags")
    if not isinstance(lags, list) or not lags or any(not isinstance(lag, int) or lag < 0 for lag in lags):
        raise ValueError("Registered lags must be a non-empty list of non-negative integers")
    if len(lags) != len(set(lags)):
        raise ValueError("Registered lags must be unique")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def chronological_windows(
    observations: int,
    rolling_window: int,
    rolling_step: int,
    expanding_minimum: int,
    expanding_step: int,
) -> list[dict]:
    """Return fixed rolling and expanding slices, including the latest endpoint."""
    if observations < max(rolling_window, expanding_minimum):
        raise ValueError("Insufficient observations for stability windows")
    windows: list[dict] = []
    rolling_starts = list(range(0, observations - rolling_window + 1, rolling_step))
    if rolling_starts[-1] != observations - rolling_window:
        rolling_starts.append(observations - rolling_window)
    for index, start in enumerate(rolling_starts, 1):
        windows.append({"mode": "rolling", "window_number": index, "start": start, "end": start + rolling_window})
    expanding_ends = list(range(expanding_minimum, observations + 1, expanding_step))
    if expanding_ends[-1] != observations:
        expanding_ends.append(observations)
    for index, end in enumerate(expanding_ends, 1):
        windows.append({"mode": "expanding", "window_number": index, "start": 0, "end": end})
    return windows


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _band_values(spectra: list[dict], bands: list[dict]) -> list[dict]:
    rows = []
    total_factor_power = sum(row["factor_power"] for row in spectra)
    for band_index, band in enumerate(bands):
        selected = [
            row for row in spectra
            if band["minimum_period"] <= row["period_sessions"]
            and (row["period_sessions"] < band["maximum_period"]
                 or (band_index == len(bands) - 1 and row["period_sessions"] <= band["maximum_period"]))
        ]
        if not selected:
            raise ValueError(f"No spectral bins in stability band {band['name']}")
        peak = max(selected, key=lambda row: row["coherence"])
        rows.append({
            "band": band["name"],
            "mean_coherence": fmean(row["coherence"] for row in selected),
            "peak_coherence": peak["coherence"],
            "peak_period_sessions": peak["period_sessions"],
            "factor_lead_sessions_at_peak": peak["factor_lead_sessions"],
            "factor_band_power_share": (
                sum(row["factor_power"] for row in selected) / total_factor_power if total_factor_power else 0.0
            ),
            "welch_windows": peak["windows"],
        })
    return rows


def _summarize_bands(window_rows: list[dict], band_order: list[str]) -> tuple[list[dict], list[dict]]:
    summary, leaders = [], []
    modes = ("rolling", "expanding")
    factors = sorted({row["factor"] for row in window_rows})
    for mode in modes:
        for factor in factors:
            factor_rows = [row for row in window_rows if row["mode"] == mode and row["factor"] == factor]
            top_counts = {band: 0 for band in band_order}
            for window_number in sorted({row["window_number"] for row in factor_rows}):
                candidates = [row for row in factor_rows if row["window_number"] == window_number]
                winner = max(candidates, key=lambda row: (row["mean_coherence"], -band_order.index(row["band"])))
                top_counts[winner["band"]] += 1
            for band in band_order:
                rows = [row for row in factor_rows if row["band"] == band]
                coherence = [row["mean_coherence"] for row in rows]
                leads = [row["factor_lead_sessions_at_peak"] for row in rows]
                positive_share = sum(value > 0 for value in leads) / len(leads)
                item = {
                    "mode": mode, "factor": factor, "band": band, "window_count": len(rows),
                    "median_mean_coherence": median(coherence),
                    "q10_mean_coherence": _quantile(coherence, 0.10),
                    "q90_mean_coherence": _quantile(coherence, 0.90),
                    "top_band_share": top_counts[band] / len(rows),
                    "median_peak_period_sessions": median(row["peak_period_sessions"] for row in rows),
                    "median_factor_lead_sessions": median(leads),
                    "positive_lead_share": positive_share,
                    "lead_sign_consistency": max(positive_share, 1 - positive_share),
                }
                summary.append(item)
            leaders.append(max(
                (row for row in summary if row["mode"] == mode and row["factor"] == factor),
                key=lambda row: (row["top_band_share"], row["median_mean_coherence"], -band_order.index(row["band"])),
            ))
    return summary, leaders


def _summarize_lags(window_rows: list[dict]) -> list[dict]:
    summary = []
    for mode in ("rolling", "expanding"):
        for factor in sorted({row["factor"] for row in window_rows}):
            for lag in sorted({row["lag_sessions"] for row in window_rows}):
                rows = [row for row in window_rows
                        if row["mode"] == mode and row["factor"] == factor and row["lag_sessions"] == lag]
                values = [row["correlation"] for row in rows]
                positive_share = sum(value > 0 for value in values) / len(values)
                summary.append({
                    "mode": mode, "factor": factor, "lag_sessions": lag, "window_count": len(rows),
                    "median_correlation": median(values),
                    "q10_correlation": _quantile(values, 0.10),
                    "q90_correlation": _quantile(values, 0.90),
                    "positive_share": positive_share,
                    "sign_consistency": max(positive_share, 1 - positive_share),
                })
    return summary


def build_spectral_stability(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    spectral_path = root() / "reports" / "denn_spectral.json"
    if not spectral_path.exists():
        raise ValueError("Deterministic spectral baseline is required")
    spectral = json.loads(spectral_path.read_text(encoding="utf-8"))
    spectral_config = yaml.safe_load(SPECTRAL_CONFIG_PATH.read_bytes())
    preprocessed_path = root() / spectral["files"]["preprocessed"]
    columns = spectral["series"]
    with duckdb.connect() as connection:
        selected = ",".join(f'"{column}"' for column in columns)
        rows = connection.execute(
            f'SELECT observation_date,{selected} FROM read_parquet(?) ORDER BY observation_date',
            [str(preprocessed_path)],
        ).fetchall()
    dates = [row[0] for row in rows]
    values = {name: [float(row[index + 1]) for row in rows] for index, name in enumerate(columns)}
    target_name = spectral["target"]
    factors = [name for name in columns if name != target_name]
    windows = chronological_windows(
        len(rows), config["rolling_window_sessions"], config["rolling_step_sessions"],
        config["expanding_minimum_sessions"], config["expanding_step_sessions"],
    )
    window_band_rows, window_lag_rows = [], []
    for window in windows:
        start, end = window["start"], window["end"]
        target = values[target_name][start:end]
        for factor in factors:
            factor_values = values[factor][start:end]
            spectra = welch_spectra(
                factor_values, target, spectral_config["window_length"], spectral_config["window_step"]
            )
            band_values = _band_values(spectra, spectral_config["bands"])
            winning_band = max(band_values, key=lambda row: row["mean_coherence"])["band"]
            common = {
                "mode": window["mode"], "window_number": window["window_number"],
                "window_start": dates[start], "window_end": dates[end - 1], "observations": end - start,
                "factor": factor, "target": target_name,
            }
            for band in band_values:
                window_band_rows.append({**common, **band, "is_top_band": band["band"] == winning_band})
            for lag in config["registered_lags"]:
                left = factor_values[:-lag] if lag else factor_values
                right = target[lag:] if lag else target
                value = correlation(left, right)
                if value is None:
                    raise ValueError("Registered lag correlation is undefined")
                window_lag_rows.append({
                    **common, "lag_sessions": lag, "correlation": value, "lag_observations": len(left),
                })
    band_order = [band["name"] for band in spectral_config["bands"]]
    band_summary, leaders = _summarize_bands(window_band_rows, band_order)
    lag_summary = _summarize_lags(window_lag_rows)
    normalized = {
        "input_dataset_id": spectral["dataset_id"], "input_sha256": spectral["normalized_sha256"],
        "config_sha256": config_hash, "window_bands": window_band_rows, "band_summary": band_summary,
        "window_lags": window_lag_rows, "lag_summary": lag_summary,
    }
    normalized_hash = hashlib.sha256(json.dumps(
        normalized, sort_keys=True, default=str, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    folder = root() / "gold" / "denn_spectral_stability" / dataset_id
    files = {
        "window_bands": (folder / "window_band_metrics.parquet").relative_to(root()).as_posix(),
        "band_summary": (folder / "band_stability.parquet").relative_to(root()).as_posix(),
        "window_lags": (folder / "window_lag_metrics.parquet").relative_to(root()).as_posix(),
        "lag_summary": (folder / "lag_stability.parquet").relative_to(root()).as_posix(),
    }
    _write_parquet(window_band_rows, root() / files["window_bands"], "mode,factor,window_number,band")
    _write_parquet(band_summary, root() / files["band_summary"], "mode,factor,band")
    _write_parquet(window_lag_rows, root() / files["window_lags"], "mode,factor,window_number,lag_sessions")
    _write_parquet(lag_summary, root() / files["lag_summary"], "mode,factor,lag_sessions")
    rolling_count = sum(window["mode"] == "rolling" for window in windows)
    expanding_count = len(windows) - rolling_count
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "normalized_sha256": normalized_hash,
        "generated_at": datetime.now(UTC).isoformat(), "input_dataset_id": spectral["dataset_id"],
        "input_sha256": spectral["normalized_sha256"], "config_sha256": config_hash,
        "date_from": str(dates[0]), "date_to": str(dates[-1]), "observations": len(rows),
        "factor_count": len(factors), "bands": band_order, "registered_lags": config["registered_lags"],
        "rolling_window_sessions": config["rolling_window_sessions"],
        "rolling_step_sessions": config["rolling_step_sessions"],
        "rolling_overlap_share": 1 - config["rolling_step_sessions"] / config["rolling_window_sessions"],
        "expanding_minimum_sessions": config["expanding_minimum_sessions"],
        "expanding_step_sessions": config["expanding_step_sessions"],
        "rolling_windows": rolling_count, "expanding_windows": expanding_count,
        "window_band_rows": len(window_band_rows), "window_lag_rows": len(window_lag_rows),
        "leaders": leaders,
        "lag_one": [row for row in lag_summary if row["lag_sessions"] == 1],
        "files": files, "strict_pit_eligible": False, "inference_status": "descriptive_non_strict",
        "limitations": [
            "Rolling and expanding windows diagnose historical stability; they are not model validation folds.",
            "Rolling windows overlap and expanding windows are nested, so window shares are not independent probabilities.",
            "Band leaders are selected descriptively within each window and carry no significance claim.",
            "Bands contain different numbers of frequency bins; narrow slow bands can have higher sampling variability.",
            "Phase-derived lead is ambiguous modulo period; sign consistency does not establish causality.",
            "Inputs are current-history downloads and are not strict point-in-time vintages.",
            "Periods are observed common-grid sessions rather than calendar days.",
        ],
    }
    atomic_json(root() / "reports" / "denn_spectral_stability.json", report)
    return report
