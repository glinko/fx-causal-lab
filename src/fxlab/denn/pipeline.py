"""Point-in-time-shaped snapshots, causal deterministic features and walk-forward baseline."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, time, timezone
from pathlib import Path
from statistics import fmean
from uuid import uuid4

import duckdb
import yaml

from ..store import atomic_json, root

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "denn.yaml"
RAW_COLUMNS = ["EURUSD_REF", "US_2Y", "EA_2Y", "US_10Y", "EA_10Y", "BRENT", "WTI", "VIX"]


def age_decay(age: float, half_life: float) -> float:
    if age < 0 or half_life <= 0:
        raise ValueError("age must be non-negative and half-life positive")
    return math.exp(-math.log(2.0) * age / half_life)


def exponential_memory(values: list[float], half_life: float) -> list[float]:
    if not values:
        return []
    persistence = age_decay(1.0, half_life)
    memory = [float(values[0])]
    for value in values[1:]:
        memory.append(persistence * memory[-1] + (1.0 - persistence) * float(value))
    return memory


def _zscore(values: list[float], index: int, window: int) -> float | None:
    if index + 1 < window:
        return None
    sample = values[index - window + 1:index + 1]
    mean = fmean(sample)
    variance = fmean((value - mean) ** 2 for value in sample)
    return 0.0 if variance <= 1e-18 else (values[index] - mean) / math.sqrt(variance)


def _log_return(values: list[float], index: int, lag: int) -> float | None:
    if index < lag:
        return None
    start, end = values[index - lag], values[index]
    if start <= 0 or end <= 0:
        raise ValueError("log-return input must be positive")
    return math.log(end / start)


def _asinh_change(values: list[float], index: int, lag: int, scale: float = 20.0) -> float | None:
    if index < lag:
        return None
    if scale <= 0:
        raise ValueError("asinh scale must be positive")
    return math.asinh(values[index] / scale) - math.asinh(values[index - lag] / scale)


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-baseline-1":
        raise ValueError("Unsupported DENN config version")
    if set(config["nodes"]) != set(RAW_COLUMNS):
        raise ValueError("DENN node config does not match common grid")
    if any(not {"node_type", "source", "unit", "half_life_sessions"} <= set(details)
           for details in config["nodes"].values()):
        raise ValueError("Each DENN node requires type, source, unit and half-life")
    if sorted(config["horizons"]) != [1, 5, 20, 60]:
        raise ValueError("DENN horizons must remain 1/5/20/60")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _load_common() -> tuple[list[date], dict[str, list[float]], dict]:
    report_path = root() / "reports" / "data_coverage.json"
    if not report_path.exists():
        raise ValueError("Open-data coverage must be built first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    path = root() / report["files"]["common_d1"]
    quoted = ",".join(f'"{column}"' for column in RAW_COLUMNS)
    with duckdb.connect() as connection:
        rows = connection.execute(
            f"SELECT observation_date,{quoted} FROM read_parquet(?) ORDER BY observation_date", [str(path)]
        ).fetchall()
    dates = [date.fromisoformat(str(row[0])) for row in rows]
    if len(dates) < 1000 or dates != sorted(set(dates)):
        raise ValueError("Common grid is too short, unordered or duplicated")
    values = {column: [float(row[index + 1]) for row in rows] for index, column in enumerate(RAW_COLUMNS)}
    if any(not math.isfinite(value) for series in values.values() for value in series):
        raise ValueError("Common grid contains a non-finite value")
    return dates, values, report


def _snapshots(dates: list[date], values: dict[str, list[float]], config: dict, coverage: dict) -> list[dict]:
    ingested_at = datetime.fromisoformat(coverage["generated_at"])
    if ingested_at.tzinfo is None:
        raise ValueError("Coverage generated_at must be timezone-aware")
    rows = []
    for index, day in enumerate(dates):
        for node in RAW_COLUMNS:
            rows.append({
                "snapshot_date": day,
                "event_time": datetime.combine(day, time.min, UTC),
                "node_id": node,
                "node_type": config["nodes"][node]["node_type"],
                "value": values[node][index],
                "unit": config["nodes"][node]["unit"],
                "source": config["nodes"][node]["source"],
                "published_at": None,
                "available_at": None,
                "ingested_at": ingested_at,
                "revision_id": None,
                "source_snapshot_id": coverage["dataset_id"],
                "time_quality": "known_observation_day",
                "quality": "current_history_unknown_vintage",
                "strict_pit_eligible": False,
                "half_life_sessions": float(config["nodes"][node]["half_life_sessions"]),
            })
    return rows


def _features(dates: list[date], values: dict[str, list[float]], config: dict) -> list[dict]:
    spread_2y = [us - ea for us, ea in zip(values["US_2Y"], values["EA_2Y"])]
    spread_10y = [us - ea for us, ea in zip(values["US_10Y"], values["EA_10Y"])]
    memory_inputs = {
        "spread_2y": (spread_2y, 20.0),
        "spread_10y": (spread_10y, 60.0),
        "brent": (values["BRENT"], float(config["nodes"]["BRENT"]["half_life_sessions"])),
        "wti": (values["WTI"], float(config["nodes"]["WTI"]["half_life_sessions"])),
        "vix": (values["VIX"], float(config["nodes"]["VIX"]["half_life_sessions"])),
        "eurusd": (values["EURUSD_REF"], float(config["nodes"]["EURUSD_REF"]["half_life_sessions"])),
    }
    memories = {name: exponential_memory(series, half_life) for name, (series, half_life) in memory_inputs.items()}
    maximum_horizon = max(config["horizons"])
    rows = []
    for index in range(config["warmup_sessions"] - 1, len(dates) - maximum_horizon):
        row = {
            "feature_date": dates[index],
            "prediction_boundary": "after_daily_observation_non_strict",
            "strict_pit_eligible": False,
            "spread_2y": spread_2y[index],
            "spread_10y": spread_10y[index],
            "spread_2y_z60": _zscore(spread_2y, index, 60),
            "spread_10y_z60": _zscore(spread_10y, index, 60),
            "brent_asinh_change_20d": _asinh_change(values["BRENT"], index, 20),
            "wti_asinh_change_20d": _asinh_change(values["WTI"], index, 20),
            "vix_z60": _zscore(values["VIX"], index, 60),
            "eurusd_momentum_20d": _log_return(values["EURUSD_REF"], index, 20),
        }
        for name, memory in memories.items():
            row[f"memory_{name}"] = memory[index]
        for horizon in config["horizons"]:
            row[f"target_start_{horizon}d"] = dates[index + 1]
            row[f"target_end_{horizon}d"] = dates[index + horizon]
            row[f"target_return_{horizon}d"] = math.log(values["EURUSD_REF"][index + horizon] / values["EURUSD_REF"][index])
        if any(row[name] is None or not math.isfinite(float(row[name])) for name in config["baseline_features"]):
            raise ValueError("Invalid deterministic feature")
        rows.append(row)
    return rows


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    size = len(vector)
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("Singular regression matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [value - factor * base for value, base in zip(augmented[row], augmented[column])]
    return [augmented[index][-1] for index in range(size)]


def _fit_ridge(rows: list[dict], feature_names: list[str], target: str, ridge: float) -> dict:
    means = [fmean(float(row[name]) for row in rows) for name in feature_names]
    scales = []
    for index, name in enumerate(feature_names):
        variance = fmean((float(row[name]) - means[index]) ** 2 for row in rows)
        scales.append(math.sqrt(variance) if variance > 1e-18 else 1.0)
    width = len(feature_names) + 1
    gram = [[0.0] * width for _ in range(width)]
    rhs = [0.0] * width
    for row in rows:
        vector = [1.0] + [(float(row[name]) - means[index]) / scales[index] for index, name in enumerate(feature_names)]
        outcome = float(row[target])
        for left in range(width):
            rhs[left] += vector[left] * outcome
            for right in range(width):
                gram[left][right] += vector[left] * vector[right]
    for index in range(1, width):
        gram[index][index] += ridge
    return {"coefficients": _solve(gram, rhs), "means": means, "scales": scales}


def _predict(model: dict, row: dict, feature_names: list[str]) -> float:
    vector = [1.0] + [(float(row[name]) - model["means"][index]) / model["scales"][index]
                      for index, name in enumerate(feature_names)]
    return sum(coefficient * value for coefficient, value in zip(model["coefficients"], vector))


def _mse(actual: list[float], predicted: list[float]) -> float:
    return fmean((left - right) ** 2 for left, right in zip(actual, predicted))


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3:
        return None
    left_mean, right_mean = fmean(left), fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_sum = sum((a - left_mean) ** 2 for a in left)
    right_sum = sum((b - right_mean) ** 2 for b in right)
    return None if left_sum <= 0 or right_sum <= 0 else numerator / math.sqrt(left_sum * right_sum)


def _walk_forward(features: list[dict], config: dict) -> tuple[list[dict], list[dict], list[dict]]:
    feature_names = list(config["baseline_features"])
    folds, predictions = [], []
    years = range(features[0]["feature_date"].year + 6, features[-1]["feature_date"].year + 1)
    for horizon in config["horizons"]:
        target = f"target_return_{horizon}d"
        target_end = f"target_end_{horizon}d"
        for test_year in years:
            validation_start = date(test_year - 1, 1, 1)
            test_start, test_end = date(test_year, 1, 1), date(test_year, 12, 31)
            selection_train = [row for row in features if row[target_end] < validation_start]
            validation = [row for row in features if validation_start <= row["feature_date"] < test_start and row[target_end] < test_start]
            fit_rows = [row for row in features if row[target_end] < test_start]
            test_rows = [row for row in features if test_start <= row["feature_date"] <= test_end and row[target_end] <= test_end]
            if (len(selection_train) < config["minimum_train_rows"] or
                    len(validation) < config["minimum_validation_rows"] or
                    len(test_rows) < config["minimum_test_rows"]):
                continue
            best_lambda, best_validation_mse = None, math.inf
            for ridge in config["ridge_lambdas"]:
                candidate = _fit_ridge(selection_train, feature_names, target, float(ridge))
                score = _mse([float(row[target]) for row in validation],
                             [_predict(candidate, row, feature_names) for row in validation])
                if score < best_validation_mse:
                    best_lambda, best_validation_mse = float(ridge), score
            model = _fit_ridge(fit_rows, feature_names, target, best_lambda)
            historical_mean = fmean(float(row[target]) for row in fit_rows)
            actual = [float(row[target]) for row in test_rows]
            predicted = [_predict(model, row, feature_names) for row in test_rows]
            baseline = [historical_mean] * len(test_rows)
            model_mse, mean_mse = _mse(actual, predicted), _mse(actual, baseline)
            fold_id = f"{test_year}-{horizon}d"
            folds.append({
                "fold_id": fold_id, "test_year": test_year, "horizon_sessions": horizon,
                "train_rows": len(selection_train), "validation_rows": len(validation), "fit_rows": len(fit_rows),
                "test_rows": len(test_rows), "selected_lambda": best_lambda,
                "validation_mse": best_validation_mse, "test_mse": model_mse,
                "mean_baseline_mse": mean_mse, "skill_vs_mean": 1.0 - model_mse / mean_mse if mean_mse else None,
                "test_mae": fmean(abs(a - p) for a, p in zip(actual, predicted)),
                "correlation": _correlation(actual, predicted),
                "sign_accuracy": fmean((a >= 0) == (p >= 0) for a, p in zip(actual, predicted)),
            })
            for row, actual_value, predicted_value in zip(test_rows, actual, predicted):
                predictions.append({
                    "fold_id": fold_id, "feature_date": row["feature_date"],
                    "target_start_date": row[f"target_start_{horizon}d"], "target_end_date": row[target_end],
                    "horizon_sessions": horizon, "actual_return": actual_value,
                    "predicted_return": predicted_value, "mean_baseline_return": historical_mean,
                    "strict_pit_eligible": False,
                })
    aggregate = []
    for horizon in config["horizons"]:
        rows = [row for row in predictions if row["horizon_sessions"] == horizon]
        actual = [row["actual_return"] for row in rows]
        predicted = [row["predicted_return"] for row in rows]
        baseline = [row["mean_baseline_return"] for row in rows]
        model_mse, mean_mse = _mse(actual, predicted), _mse(actual, baseline)
        aggregate.append({
            "horizon_sessions": horizon, "out_of_sample_rows": len(rows), "folds": sum(1 for row in folds if row["horizon_sessions"] == horizon),
            "mse": model_mse, "mean_baseline_mse": mean_mse,
            "skill_vs_mean": 1.0 - model_mse / mean_mse if mean_mse else None,
            "mae": fmean(abs(a - p) for a, p in zip(actual, predicted)),
            "correlation": _correlation(actual, predicted),
            "sign_accuracy": fmean((a >= 0) == (p >= 0) for a, p in zip(actual, predicted)),
        })
    if not folds or not predictions:
        raise ValueError("No valid walk-forward folds")
    return folds, predictions, aggregate


def _write_parquet(rows: list[dict], path: Path, order_by: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = path.with_suffix(f".{uuid4().hex}.json.tmp")
    parquet_tmp = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    json_tmp.write_text("\n".join(json.dumps(row, default=str, allow_nan=False) for row in rows), encoding="utf-8")
    try:
        with duckdb.connect() as connection:
            connection.execute("CREATE TABLE output AS SELECT * FROM read_json_auto(?)", [str(json_tmp)])
            connection.execute(f"COPY (SELECT * FROM output ORDER BY {order_by}) TO ? (FORMAT PARQUET)", [str(parquet_tmp)])
        parquet_tmp.replace(path)
    finally:
        json_tmp.unlink(missing_ok=True)
        parquet_tmp.unlink(missing_ok=True)


def _write_snapshots(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    json_tmp = path.with_suffix(f".{uuid4().hex}.json.tmp")
    parquet_tmp = path.with_suffix(f".{uuid4().hex}.parquet.tmp")
    json_tmp.write_text("\n".join(json.dumps(row, default=str, allow_nan=False) for row in rows), encoding="utf-8")
    try:
        with duckdb.connect() as connection:
            connection.execute(
                "CREATE TABLE snapshots AS SELECT snapshot_date::DATE AS snapshot_date,"
                "event_time::TIMESTAMPTZ AS event_time,node_id::VARCHAR AS node_id,node_type::VARCHAR AS node_type,"
                "value::DOUBLE AS value,unit::VARCHAR AS unit,source::VARCHAR AS source,"
                "published_at::TIMESTAMPTZ AS published_at,available_at::TIMESTAMPTZ AS available_at,"
                "ingested_at::TIMESTAMPTZ AS ingested_at,revision_id::VARCHAR AS revision_id,"
                "source_snapshot_id::VARCHAR AS source_snapshot_id,time_quality::VARCHAR AS time_quality,"
                "quality::VARCHAR AS quality,"
                "strict_pit_eligible::BOOLEAN AS strict_pit_eligible,half_life_sessions::DOUBLE AS half_life_sessions "
                "FROM read_json_auto(?)", [str(json_tmp)])
            connection.execute("COPY (SELECT * FROM snapshots ORDER BY snapshot_date,node_id) TO ? (FORMAT PARQUET)",
                               [str(parquet_tmp)])
        parquet_tmp.replace(path)
    finally:
        json_tmp.unlink(missing_ok=True)
        parquet_tmp.unlink(missing_ok=True)


def build_denn_baseline(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    dates, values, coverage = _load_common()
    snapshots = _snapshots(dates, values, config, coverage)
    features = _features(dates, values, config)
    folds, predictions, aggregate = _walk_forward(features, config)
    normalized = {
        "input_dataset_id": coverage["dataset_id"], "input_sha256": coverage["normalized_sha256"],
        "config_sha256": config_hash, "features": features, "folds": folds,
        "predictions": predictions, "aggregate": aggregate,
    }
    normalized_hash = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    silver = root() / "silver" / "denn" / dataset_id
    gold = root() / "gold" / "denn" / dataset_id
    files = {
        "snapshots": (silver / "snapshots.parquet").relative_to(root()).as_posix(),
        "features": (gold / "features.parquet").relative_to(root()).as_posix(),
        "folds": (gold / "fold_metrics.parquet").relative_to(root()).as_posix(),
        "predictions": (gold / "predictions.parquet").relative_to(root()).as_posix(),
    }
    _write_snapshots(snapshots, root() / files["snapshots"])
    _write_parquet(features, root() / files["features"], "feature_date")
    _write_parquet(folds, root() / files["folds"], "horizon_sessions,test_year")
    _write_parquet(predictions, root() / files["predictions"], "horizon_sessions,feature_date")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "normalized_sha256": normalized_hash,
        "generated_at": datetime.now(UTC).isoformat(), "input_dataset_id": coverage["dataset_id"],
        "input_sha256": coverage["normalized_sha256"], "config_sha256": config_hash,
        "date_from": str(dates[0]), "date_to": str(dates[-1]), "snapshot_rows": len(snapshots),
        "feature_rows": len(features), "fold_rows": len(folds), "prediction_rows": len(predictions),
        "feature_names": config["baseline_features"], "horizons": config["horizons"],
        "decay_priors": {node: {"half_life_sessions": details["half_life_sessions"],
                                  "one_session_persistence": age_decay(1, details["half_life_sessions"])}
                         for node, details in config["nodes"].items()},
        "aggregate_metrics": aggregate, "files": files, "strict_pit_eligible": False,
        "limitations": [
            "All inputs are current-history downloads with unknown historical availability; results are non-strict.",
            "The long EUR/USD input is the ECB daily reference rate, not an executable close or OHLC bar.",
            "Feature transforms use only observations at or before feature_date; labels start on the following common-grid date.",
            "Validation selects ridge lambda from the preceding calendar year; training rows are purged by target_end_date.",
            "Metrics are descriptive out-of-sample diagnostics, not a trading strategy or causal result.",
        ],
    }
    atomic_json(root() / "reports" / "denn_baseline.json", report)
    return report
