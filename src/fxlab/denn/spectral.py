"""Deterministic descriptive spectral diagnostics on the common D1 session grid."""
from __future__ import annotations

import cmath
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

import duckdb
import yaml

from ..store import atomic_json, root
from .pipeline import _write_parquet

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "spectral.yaml"


def fft(values: list[complex | float]) -> list[complex]:
    """Radix-2 FFT with an explicit power-of-two contract."""
    size = len(values)
    if size == 0 or size & (size - 1):
        raise ValueError("FFT length must be a non-zero power of two")
    output = [complex(value) for value in values]
    target = 0
    for source in range(1, size):
        bit = size >> 1
        while target & bit:
            target ^= bit
            bit >>= 1
        target ^= bit
        if source < target:
            output[source], output[target] = output[target], output[source]
    length = 2
    while length <= size:
        step = cmath.exp(-2j * math.pi / length)
        half = length // 2
        for start in range(0, size, length):
            rotation = 1 + 0j
            for offset in range(half):
                even = output[start + offset]
                odd = rotation * output[start + offset + half]
                output[start + offset] = even + odd
                output[start + offset + half] = even - odd
                rotation *= step
        length *= 2
    return output


def correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    left_mean, right_mean = fmean(left), fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_ss = sum((a - left_mean) ** 2 for a in left)
    right_ss = sum((b - right_mean) ** 2 for b in right)
    return None if left_ss <= 1e-18 or right_ss <= 1e-18 else numerator / math.sqrt(left_ss * right_ss)


def lag_correlations(factor: list[float], target: list[float], maximum_lag: int) -> list[dict]:
    """Positive lag means factor[t] is compared with target[t+lag]."""
    if len(factor) != len(target) or maximum_lag < 0 or len(factor) <= maximum_lag + 2:
        raise ValueError("Invalid lag-correlation inputs")
    rows = []
    for lag in range(-maximum_lag, maximum_lag + 1):
        if lag > 0:
            left, right = factor[:-lag], target[lag:]
        elif lag < 0:
            left, right = factor[-lag:], target[:lag]
        else:
            left, right = factor, target
        rows.append({"lag_sessions": lag, "correlation": correlation(left, right), "observations": len(left)})
    return rows


def haar_energy(values: list[float], levels: int) -> list[dict]:
    if levels < 1 or len(values) < 2 ** levels:
        raise ValueError("Insufficient observations for requested Haar levels")
    approximation = [float(value) for value in values]
    energies = []
    root_two = math.sqrt(2.0)
    for level in range(1, levels + 1):
        if len(approximation) % 2:
            approximation = approximation[:-1]
        next_approximation, details = [], []
        for index in range(0, len(approximation), 2):
            left, right = approximation[index], approximation[index + 1]
            next_approximation.append((left + right) / root_two)
            details.append((left - right) / root_two)
        energies.append({"level": level, "nominal_period_sessions": 2 ** level,
                         "energy": sum(value * value for value in details), "coefficients": len(details)})
        approximation = next_approximation
    total = sum(row["energy"] for row in energies) + sum(value * value for value in approximation)
    for row in energies:
        row["energy_share"] = row["energy"] / total if total else 0.0
    return energies


def welch_spectra(factor: list[float], target: list[float], window_length: int, step: int) -> list[dict]:
    if window_length < 8 or window_length & (window_length - 1) or step < 1:
        raise ValueError("Welch window must be a power of two and step positive")
    if len(factor) != len(target) or len(factor) < window_length:
        raise ValueError("Insufficient aligned observations for Welch spectra")
    bins = window_length // 2
    factor_power = [0.0] * bins
    target_power = [0.0] * bins
    cross = [0j] * bins
    windows = 0
    weights = [0.5 - 0.5 * math.cos(2 * math.pi * index / (window_length - 1))
               for index in range(window_length)]
    for start in range(0, len(factor) - window_length + 1, step):
        factor_window = factor[start:start + window_length]
        target_window = target[start:start + window_length]
        factor_mean, target_mean = fmean(factor_window), fmean(target_window)
        factor_fft = fft([(value - factor_mean) * weights[index] for index, value in enumerate(factor_window)])
        target_fft = fft([(value - target_mean) * weights[index] for index, value in enumerate(target_window)])
        for index in range(1, bins + 1):
            left, right = factor_fft[index], target_fft[index]
            slot = index - 1
            factor_power[slot] += abs(left) ** 2
            target_power[slot] += abs(right) ** 2
            cross[slot] += left.conjugate() * right
        windows += 1
    rows = []
    for slot in range(bins):
        frequency = (slot + 1) / window_length
        p_factor = factor_power[slot] / windows
        p_target = target_power[slot] / windows
        cross_value = cross[slot] / windows
        denominator = p_factor * p_target
        coherence = min(1.0, abs(cross_value) ** 2 / denominator) if denominator > 1e-18 else 0.0
        phase = cmath.phase(cross_value)
        rows.append({
            "frequency": frequency, "period_sessions": 1.0 / frequency,
            "factor_power": p_factor, "target_power": p_target,
            "coherence": coherence, "phase_radians": phase,
            "factor_lead_sessions": -phase / (2 * math.pi * frequency), "windows": windows,
        })
    return rows


def _standardize(values: list[float]) -> list[float]:
    mean = fmean(values)
    variance = fmean((value - mean) ** 2 for value in values)
    if variance <= 1e-18:
        raise ValueError("Cannot standardize a constant series")
    scale = math.sqrt(variance)
    return [(value - mean) / scale for value in values]


def _load_config(path: Path) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-spectral-1":
        raise ValueError("Unsupported spectral config version")
    if config["window_length"] & (config["window_length"] - 1):
        raise ValueError("Spectral window length must be a power of two")
    if sum(details.get("role") == "target" for details in config["series"].values()) != 1:
        raise ValueError("Exactly one spectral target is required")
    if config["window_step"] < 1 or config["maximum_lag_sessions"] < 0 or config["wavelet_levels"] < 1:
        raise ValueError("Spectral step, lag and wavelet levels are invalid")
    previous_maximum = None
    band_names = set()
    for band in config["bands"]:
        if band["name"] in band_names or band["minimum_period"] < 2 or band["maximum_period"] <= band["minimum_period"]:
            raise ValueError("Spectral bands must be unique positive period intervals")
        if previous_maximum is not None and band["minimum_period"] != previous_maximum:
            raise ValueError("Spectral bands must be contiguous and ordered")
        band_names.add(band["name"])
        previous_maximum = band["maximum_period"]
    if config["bands"][-1]["maximum_period"] > config["window_length"]:
        raise ValueError("Spectral period exceeds the Welch window")
    return config, hashlib.sha256(body).hexdigest()


def _load_inputs() -> tuple[list, dict[str, list[float]], dict, dict]:
    coverage_path = root() / "reports" / "data_coverage.json"
    baseline_path = root() / "reports" / "denn_baseline.json"
    if not coverage_path.exists() or not baseline_path.exists():
        raise ValueError("Data coverage and deterministic DENN baseline are required")
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline["input_dataset_id"] != coverage["dataset_id"]:
        raise ValueError("DENN baseline does not match current data coverage")
    path = root() / coverage["files"]["common_d1"]
    with duckdb.connect() as connection:
        rows = connection.execute(
            'SELECT observation_date,"EURUSD_REF","US_2Y","EA_2Y","US_10Y","EA_10Y","BRENT","WTI","VIX" '
            'FROM read_parquet(?) ORDER BY observation_date', [str(path)]).fetchall()
    dates = [row[0] for row in rows]
    values = {
        "EURUSD_REF": [float(row[1]) for row in rows],
        "US_EA_2Y": [float(row[2] - row[3]) for row in rows],
        "US_EA_10Y": [float(row[4] - row[5]) for row in rows],
        "BRENT": [float(row[6]) for row in rows], "WTI": [float(row[7]) for row in rows],
        "VIX": [float(row[8]) for row in rows],
    }
    return dates, values, coverage, baseline


def _transform(values: list[float], method: str, scale: float | None = None) -> list[float]:
    output = []
    for previous, current in zip(values, values[1:]):
        if method == "difference":
            output.append(current - previous)
        elif method == "log_difference":
            if previous <= 0 or current <= 0:
                raise ValueError("Log difference requires positive values")
            output.append(math.log(current / previous))
        elif method == "asinh_difference":
            if scale is None or scale <= 0:
                raise ValueError("asinh difference requires positive scale")
            output.append(math.asinh(current / scale) - math.asinh(previous / scale))
        else:
            raise ValueError(f"Unsupported spectral transform: {method}")
    return output


def build_spectral_baseline(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    dates, raw, coverage, baseline = _load_inputs()
    transformed = {}
    preprocessed_rows = []
    for series_id, details in config["series"].items():
        values = _transform(raw[details["source"]], details["transform"], details.get("scale"))
        transformed[series_id] = _standardize(values)
    for index, day in enumerate(dates[1:]):
        preprocessed_rows.append({"observation_date": day, **{name: values[index] for name, values in transformed.items()}})

    target_name = next(name for name, details in config["series"].items() if details["role"] == "target")
    target = transformed[target_name]
    band_rows, wavelet_rows, lag_rows = [], [], []
    for series_id, values in transformed.items():
        for row in haar_energy(values, config["wavelet_levels"]):
            wavelet_rows.append({"series_id": series_id, **row})
        if series_id == target_name:
            continue
        spectra = welch_spectra(values, target, config["window_length"], config["window_step"])
        for band_index, band in enumerate(config["bands"]):
            selected = [row for row in spectra
                        if band["minimum_period"] <= row["period_sessions"] and
                        (row["period_sessions"] < band["maximum_period"] or
                         (band_index == len(config["bands"]) - 1 and
                          row["period_sessions"] <= band["maximum_period"]))]
            if not selected:
                raise ValueError(f"No spectral bins in band {band['name']}")
            peak = max(selected, key=lambda row: row["coherence"])
            band_rows.append({
                "factor": series_id, "target": target_name, "band": band["name"],
                "minimum_period_sessions": band["minimum_period"],
                "maximum_period_sessions": band["maximum_period"],
                "frequency_bins": len(selected), "windows": peak["windows"],
                "mean_coherence": fmean(row["coherence"] for row in selected),
                "peak_coherence": peak["coherence"], "peak_period_sessions": peak["period_sessions"],
                "phase_radians_at_peak": peak["phase_radians"],
                "factor_lead_sessions_at_peak": peak["factor_lead_sessions"],
                "factor_band_power_share": sum(row["factor_power"] for row in selected) /
                                           sum(row["factor_power"] for row in spectra),
                "target_band_power_share": sum(row["target_power"] for row in selected) /
                                           sum(row["target_power"] for row in spectra),
            })
        factor_lags = lag_correlations(values, target, config["maximum_lag_sessions"])
        for row in factor_lags:
            lag_rows.append({"factor": series_id, "target": target_name, **row})

    strongest_lags = []
    for factor in sorted({row["factor"] for row in lag_rows}):
        best = max((row for row in lag_rows if row["factor"] == factor), key=lambda row: abs(row["correlation"]))
        strongest_lags.append(best)
    normalized = {
        "input_dataset_id": coverage["dataset_id"], "input_sha256": coverage["normalized_sha256"],
        "baseline_dataset_id": baseline["dataset_id"], "config_sha256": config_hash,
        "preprocessed": preprocessed_rows, "bands": band_rows, "wavelets": wavelet_rows, "lags": lag_rows,
    }
    normalized_hash = hashlib.sha256(json.dumps(
        normalized, sort_keys=True, default=str, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    folder = root() / "gold" / "denn_spectral" / dataset_id
    files = {
        "preprocessed": (folder / "preprocessed.parquet").relative_to(root()).as_posix(),
        "bands": (folder / "band_metrics.parquet").relative_to(root()).as_posix(),
        "wavelets": (folder / "wavelet_energy.parquet").relative_to(root()).as_posix(),
        "lags": (folder / "lag_correlations.parquet").relative_to(root()).as_posix(),
    }
    _write_parquet(preprocessed_rows, root() / files["preprocessed"], "observation_date")
    _write_parquet(band_rows, root() / files["bands"], "factor,band")
    _write_parquet(wavelet_rows, root() / files["wavelets"], "series_id,level")
    _write_parquet(lag_rows, root() / files["lags"], "factor,lag_sessions")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "normalized_sha256": normalized_hash,
        "generated_at": datetime.now(UTC).isoformat(), "input_dataset_id": coverage["dataset_id"],
        "input_sha256": coverage["normalized_sha256"], "baseline_dataset_id": baseline["dataset_id"],
        "config_sha256": config_hash, "date_from": str(dates[1]), "date_to": str(dates[-1]),
        "observations": len(preprocessed_rows), "series": list(config["series"]),
        "target": target_name, "factor_count": len(config["series"]) - 1,
        "window_length": config["window_length"], "window_step": config["window_step"],
        "welch_windows": band_rows[0]["windows"], "bands": config["bands"],
        "band_metrics": band_rows, "strongest_lags": strongest_lags, "files": files,
        "strict_pit_eligible": False, "inference_status": "descriptive_non_strict",
        "limitations": [
            "Analysis uses an irregular common-session index; periods are observed common-grid sessions, not calendar days.",
            "Inputs are current-history downloads with unknown historical availability and are not strict point-in-time.",
            "Coherence, phase and lag maxima are full-sample descriptive diagnostics with no significance claim.",
            "Scanning frequencies and lags creates multiple-comparison risk; maxima must not be read as causal effects.",
            "ECB EUR/USD reference is not an executable close or OHLC price.",
        ],
    }
    atomic_json(root() / "reports" / "denn_spectral.json", report)
    return report
