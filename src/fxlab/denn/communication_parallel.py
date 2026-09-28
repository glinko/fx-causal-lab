"""Independent numeric predictions and AnyJev jobs for communication events."""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean

import duckdb
import yaml

from ..store import atomic_json, root
from .anyjev import build_anyjev_job, load_anyjev_config
from .market_forecast import _select_ridge
from .pipeline import _correlation, _fit_ridge, _mse, _predict, _write_parquet
from .state_vector import _fold_splits

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", "."))/"config"/"communication_parallel.yaml"
L2_CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", "."))/"config"/"anyjev_l2_chrono.yaml"


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "communication-parallel-1":
        raise ValueError("Unsupported communication parallel config")
    if sorted(config["horizons"]) != [1, 5, 20, 60]:
        raise ValueError("Communication horizons must remain 1/5/20/60")
    if config["mixing"] != {"combined_score": False, "predictions_visible_to_each_other": False,
                            "comparison_only_after_freeze": True}:
        raise ValueError("Communication model tracks must remain independent")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _load_rows(config: dict) -> tuple[list[dict], dict]:
    reports = root()/"reports"
    targets_report = json.loads((reports/"communication_targets.json").read_text(encoding="utf-8"))
    features_report = json.loads((reports/"tier_a_features.json").read_text(encoding="utf-8"))
    communications_report = json.loads((reports/"communications.json").read_text(encoding="utf-8"))
    feature_sql = ",".join(f'f."{name}"' for name in config["features"] if name != "intraday_eurusd_return_pct")
    target_sql = ",".join(f't.target_end_{h}d,t.target_return_{h}d' for h in config["horizons"])
    with duckdb.connect() as connection:
        connection.execute("SET TimeZone='UTC'")
        values = connection.execute(
            f"SELECT t.event_id,t.prediction_time,t.target_start_date,{target_sql},"
            f"t.intraday_eurusd_return_pct,{feature_sql},e.full_text "
            "FROM read_parquet(?) t JOIN read_parquet(?) f ON f.feature_date=t.target_start_date "
            "JOIN read_parquet(?) e USING(event_id) ORDER BY t.target_start_date,t.event_id",
            [str(root()/targets_report["files"]["targets"]), str(root()/features_report["files"]["features"]),
             str(root()/communications_report["files"]["events"])],
        ).fetchall()
    rows = []
    base_count = 3 + 2 * len(config["horizons"])
    for values in values:
        row = {"event_id": values[0], "prediction_time": values[1],
               "feature_date": date.fromisoformat(str(values[2]))}
        position = 3
        for horizon in config["horizons"]:
            row[f"target_end_{horizon}d"] = date.fromisoformat(str(values[position]))
            row[f"target_return_{horizon}d"] = float(values[position + 1])
            position += 2
        numeric_values = values[base_count:base_count + len(config["features"])]
        if any(value is None for value in numeric_values):
            continue
        for name, value in zip(config["features"], numeric_values):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"Non-finite communication feature: {name}")
            row[name] = number
        row["full_text"] = values[base_count + len(config["features"])]
        rows.append(row)
    if len(rows) < 1500:
        raise ValueError("Too few complete communication rows")
    inputs = {"targets": targets_report["dataset_id"], "features": features_report["dataset_id"],
              "communications": communications_report["dataset_id"]}
    return rows, inputs


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position)); upper = int(math.ceil(position))
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _direction(value: float, lower: float, upper: float) -> str:
    return "down" if value < lower else ("up" if value > upper else "flat")


def _probabilities(forecast: float, residuals: list[float], lower: float, upper: float) -> dict[str, float]:
    total = len(residuals)
    down = sum(forecast + residual < lower for residual in residuals) / total
    up = sum(forecast + residual > upper for residual in residuals) / total
    return {"down": down, "flat": 1 - down - up, "up": up}


def score_quantitative(rows: list[dict], config: dict) -> tuple[dict, list[dict]]:
    features = list(config["features"])
    minimums = (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]),
                int(config["minimum_test_rows"]))
    predictions, result = [], {}
    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    for horizon in config["horizons"]:
        target = f"target_return_{horizon}d"
        fold_count = 0
        actual_all, forecast_all = [], []
        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
                continue
            ridge = _select_ridge(split, features, target, [float(value) for value in config["ridge_lambdas"]])
            selection_model = _fit_ridge(split["selection_train"], features, target, ridge)
            residuals = [float(row[target]) - _predict(selection_model, row, features) for row in split["validation"]]
            fit_actual = [float(row[target]) for row in split["fit_rows"]]
            lower, upper = _quantile(fit_actual, 1/3), _quantile(fit_actual, 2/3)
            model = _fit_ridge(split["fit_rows"], features, target, ridge)
            for row in split["test_rows"]:
                forecast = _predict(model, row, features)
                probability = _probabilities(forecast, residuals, lower, upper)
                actual = float(row[target])
                predictions.append({
                    "event_id": row["event_id"], "prediction_time": row["prediction_time"],
                    "feature_date": row["feature_date"], "horizon_sessions": horizon,
                    "track": "quantitative", "model_id": config["tracks"]["quantitative"],
                    "forecast_return": forecast, "probability_down": probability["down"],
                    "probability_flat": probability["flat"], "probability_up": probability["up"],
                    "direction": max(probability, key=probability.get), "actual_return": actual,
                    "actual_direction": _direction(actual, lower, upper), "lower_bound": lower,
                    "upper_bound": upper, "fold_id": f"{test_year}-{horizon}d",
                })
                actual_all.append(actual); forecast_all.append(forecast)
            fold_count += 1
        if not fold_count:
            raise ValueError(f"No communication folds for {horizon}d")
        horizon_rows = [row for row in predictions if row["horizon_sessions"] == horizon]
        result[f"{horizon}d"] = {
            "predictions": len(horizon_rows), "folds": fold_count, "mse": _mse(actual_all, forecast_all),
            "correlation": _correlation(actual_all, forecast_all),
            "direction_accuracy": fmean(row["direction"] == row["actual_direction"] for row in horizon_rows),
        }
    return result, predictions


def build_anyjev_jobs(rows: list[dict], predictions: list[dict]) -> list[dict]:
    config, digest = load_anyjev_config()
    by_event_horizon = {(row["event_id"], row["horizon_sessions"]): row for row in predictions}
    jobs = []
    for row in rows:
        if not row.get("full_text"):
            continue
        for question in config["questions"]:
            horizon = int(question["horizon_sessions"])
            if (row["event_id"], horizon) not in by_event_horizon:
                continue
            snapshot = {"prediction_time": row["prediction_time"], "available_at_max": None,
                        "point_in_time_status": "non_strict_historical_text",
                        "strict_pit_eligible": False,
                        "features": {item["id"]: row.get(item["id"]) for item in config["features"]},
                        "published_text": row["full_text"]}
            job = build_anyjev_job(snapshot, question["id"], config, digest)
            numeric_metadata = by_event_horizon[(row["event_id"], horizon)]
            job.update({"event_id": row["event_id"], "track": "anyjev",
                        "model_id": "qwen38_27b_llama_l0", "actual_not_in_job": True,
                        "label_bounds": {"down_below": numeric_metadata["lower_bound"],
                                         "up_above": numeric_metadata["upper_bound"]}})
            jobs.append(job)
    return jobs


def build_communication_parallel(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    rows, inputs = _load_rows(config)
    result, predictions = score_quantitative(rows, config)
    jobs = build_anyjev_jobs(rows, predictions)
    identity = {"config_sha256": config_hash, "inputs": inputs, "result": result,
                "prediction_rows": len(predictions),
                "jobs": [{"job_id": job["job_id"], "model_id": job["model_id"],
                          "label_bounds": job["label_bounds"]} for job in jobs]}
    normalized_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    folder = root()/"gold"/"communication_parallel"/dataset_id
    _write_parquet(predictions, folder/"quantitative_predictions.parquet", "horizon_sessions,feature_date,event_id")
    folder.mkdir(parents=True, exist_ok=True)
    jobs_path = folder/"anyjev_jobs.jsonl"
    jobs_path.write_text("\n".join(json.dumps(job, ensure_ascii=False) for job in jobs) + "\n", encoding="utf-8")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "config_sha256": config_hash,
        "input_dataset_ids": inputs, "complete_event_rows": len(rows),
        "quantitative_predictions": len(predictions), "anyjev_jobs": len(jobs),
        "anyjev_predictions": 0, "result": result, "mixing": config["mixing"],
        "files": {"quantitative_predictions": (folder/"quantitative_predictions.parquet").relative_to(root()).as_posix(),
                  "anyjev_jobs": jobs_path.relative_to(root()).as_posix()},
        "status": "quantitative_ready_anyjev_jobs_ready",
        "strict_pit_eligible": False,
        "limitations": [
            "The numeric track has no access to AnyJev outputs.",
            "AnyJev jobs contain no future return, actual direction or numeric-model forecast.",
            "Historical full-text availability is not exact, so this remains a non-strict research comparison.",
        ], "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"communication_parallel.json", report)
    return report


def import_anyjev_pilot(path: Path) -> dict:
    """Join a bounded LLM pilot to frozen numeric predictions for reporting only."""
    parent = json.loads((root()/"reports"/"communication_parallel.json").read_text(encoding="utf-8"))
    responses = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not responses or len({row["job_id"] for row in responses}) != len(responses):
        raise ValueError("AnyJev pilot is empty or contains duplicate jobs")
    for row in responses:
        if row.get("track") != "anyjev" or row.get("actual_not_in_request") is not True:
            raise ValueError("Invalid AnyJev response provenance")
        if any(name in row for name in ("actual_return", "actual_direction", "forecast_return", "combined_score")):
            raise ValueError("AnyJev response contains forbidden outcome or numeric forecast")
    with duckdb.connect() as connection:
        connection.execute("SET TimeZone='UTC'")
        numeric = connection.execute(
            "SELECT event_id,prediction_time,horizon_sessions,direction,probability_down,probability_flat,"
            "probability_up,actual_return,actual_direction FROM read_parquet(?)",
            [str(root()/parent["files"]["quantitative_predictions"])],
        ).fetchall()
    numeric_by_key = {(row[0], int(row[2])): row for row in numeric}
    joined = []
    for response in responses:
        key = (response["event_id"], int(response["horizon_sessions"]))
        if key not in numeric_by_key:
            raise ValueError(f"AnyJev response has no frozen numeric peer: {key}")
        numeric_row = numeric_by_key[key]
        actual_direction = numeric_row[8]
        numeric_correct = numeric_row[3] == actual_direction
        anyjev_correct = response["direction"] == actual_direction
        comparison = ("both_correct" if numeric_correct and anyjev_correct else
                      "quantitative_only" if numeric_correct else
                      "anyjev_only" if anyjev_correct else "both_wrong")
        probability = response["probabilities"]
        joined.append({
            "job_id": response["job_id"], "event_id": response["event_id"],
            "prediction_time": numeric_row[1], "horizon_sessions": int(response["horizon_sessions"]),
            "quantitative_direction": numeric_row[3], "quantitative_probability_down": float(numeric_row[4]),
            "quantitative_probability_flat": float(numeric_row[5]), "quantitative_probability_up": float(numeric_row[6]),
            "anyjev_direction": response["direction"], "anyjev_probability_down": float(probability["down"]),
            "anyjev_probability_flat": float(probability["flat"]), "anyjev_probability_up": float(probability["up"]),
            "actual_return": float(numeric_row[7]), "actual_direction": actual_direction,
            "comparison": comparison, "state_chars_total": int(response["state_chars_total"]),
            "state_chars_used": int(response["state_chars_used"]), "elapsed_seconds": float(response["elapsed_seconds"]),
        })
    counts = {name: sum(row["comparison"] == name for row in joined)
              for name in ("both_correct", "quantitative_only", "anyjev_only", "both_wrong")}
    direction_counts = {name: sum(row["anyjev_direction"] == name for row in joined)
                        for name in ("down", "flat", "up")}
    constant_direction = sum(value > 0 for value in direction_counts.values()) == 1
    by_horizon = {}
    for horizon in (1, 5, 20, 60):
        sample = [row for row in joined if row["horizon_sessions"] == horizon]
        if sample:
            by_horizon[f"{horizon}d"] = {
                "rows": len(sample),
                "quantitative_accuracy": fmean(row["quantitative_direction"] == row["actual_direction"] for row in sample),
                "anyjev_accuracy": fmean(row["anyjev_direction"] == row["actual_direction"] for row in sample),
                "same_answer": fmean(row["quantitative_direction"] == row["anyjev_direction"] for row in sample),
            }
    identity = {"parent_dataset_id": parent["dataset_id"], "job_ids": sorted(row["job_id"] for row in joined),
                "rows": joined}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    dataset_id = digest[:20]
    folder = root()/"gold"/"communication_parallel_pilot"/dataset_id
    _write_parquet(joined, folder/"comparison.parquet", "horizon_sessions,prediction_time,event_id")
    report = {
        "dataset_id": dataset_id, "parent_dataset_id": parent["dataset_id"], "rows": len(joined),
        "counts": counts, "anyjev_direction_counts": direction_counts, "by_horizon": by_horizon,
        "average_seconds_per_job": fmean(row["elapsed_seconds"] for row in joined),
        "truncated_jobs": sum(row["state_chars_used"] < row["state_chars_total"] for row in joined),
        "files": {"comparison": (folder/"comparison.parquet").relative_to(root()).as_posix()},
        "pilot_only": True, "combined_score": False,
        "verdict": "degenerate_constant_prediction" if constant_direction else "pipeline_operational",
        "limitations": ["This is a small infrastructure pilot, not a model-quality conclusion.",
                        "Long speeches are deterministically shortened for the pilot.",
                        "Historical full-text availability remains non-strict."],
        "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"communication_parallel_pilot.json", report)
    return report


def export_anyjev_l2_bundle(config_path: Path = L2_CONFIG_PATH) -> dict:
    """Export labeled states for a chronological AnyJev L2 run without numeric forecasts."""
    config_bytes = config_path.read_bytes()
    config = yaml.safe_load(config_bytes)
    if config.get("version") != "anyjev-l2-chrono-1" or int(config["horizon_sessions"]) != 5:
        raise ValueError("Unsupported AnyJev L2 chronological config")
    if config["mixing"] != {"numeric_prediction_in_state": False, "comparison_only_after_freeze": True}:
        raise ValueError("AnyJev and numeric predictions must remain independent")
    parent = json.loads((root()/"reports"/"communication_parallel.json").read_text(encoding="utf-8"))
    jobs_path = root()/parent["files"]["anyjev_jobs"]
    jobs = [json.loads(line) for line in jobs_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    horizon = int(config["horizon_sessions"])
    with duckdb.connect() as connection:
        numeric = connection.execute(
            "SELECT event_id,horizon_sessions,feature_date,actual_direction FROM read_parquet(?) "
            "WHERE horizon_sessions=?", [str(root()/parent["files"]["quantitative_predictions"]), horizon],
        ).fetchall()
    labels = {(row[0], int(row[1])): (str(row[2]), str(row[3])) for row in numeric}
    label_index = {name: index for index, name in enumerate(config["options"])}
    rows = []
    for job in jobs:
        if int(job["horizon_sessions"]) != horizon:
            continue
        key = (job["event_id"], horizon)
        if key not in labels:
            raise ValueError(f"AnyJev job has no frozen historical outcome: {key}")
        feature_date, label = labels[key]
        state = str(job["state"])
        if any(token in state for token in ("actual_return", "actual_direction", "forecast_return",
                                             "quantitative_direction", "probability_up")):
            raise ValueError(f"Forbidden outcome or numeric prediction in state: {job['job_id']}")
        rows.append({
            "job_id": job["job_id"], "event_id": job["event_id"],
            "prediction_time": job["prediction_time"], "feature_date": feature_date,
            "horizon_sessions": horizon, "state": state, "state_sha256": job["state_sha256"],
            "state_chars_total": len(state), "label": label, "label_index": label_index[label],
            "label_is_separate_from_state": True, "numeric_prediction_in_state": False,
        })
    rows.sort(key=lambda row: (row["feature_date"], row["event_id"]))
    split_counts = {}
    for name in ("train", "validation", "test"):
        start, end = str(config["splits"][name]["from"]), str(config["splits"][name]["to"])
        split_counts[name] = sum(start <= row["feature_date"] <= end for row in rows)
        if split_counts[name] == 0:
            raise ValueError(f"Empty AnyJev L2 chronological split: {name}")
    config_hash = hashlib.sha256(config_bytes.replace(b"\r\n", b"\n")).hexdigest()
    identity = {"parent_dataset_id": parent["dataset_id"], "config_sha256": config_hash, "rows": rows}
    dataset_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    folder = root()/"gold"/"communication_anyjev_l2_bundle"/dataset_id
    folder.mkdir(parents=True, exist_ok=True)
    bundle_path = folder/"bundle.jsonl"
    bundle_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    report = {
        "dataset_id": dataset_id, "parent_dataset_id": parent["dataset_id"], "config_sha256": config_hash,
        "horizon_sessions": horizon, "rows": len(rows), "split_rows": split_counts,
        "bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "files": {"bundle": bundle_path.relative_to(root()).as_posix()},
        "numeric_prediction_in_state": False, "label_is_separate_from_state": True,
        "status": "ready_for_anyjev_l2", "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"communication_anyjev_l2_bundle.json", report)
    return report


def import_anyjev_l2(path: Path, head_path: Path | None = None) -> dict:
    """Import a chronological L2 result and compare it with the frozen numeric track."""
    parent = json.loads((root()/"reports"/"communication_parallel.json").read_text(encoding="utf-8"))
    bundle = json.loads((root()/"reports"/"communication_anyjev_l2_bundle.json").read_text(encoding="utf-8"))
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("schema_version") != "fxlab-anyjev-l2-result-1" or result.get("status") != "complete":
        raise ValueError("Invalid AnyJev L2 result")
    if result.get("bundle_sha256") != bundle["bundle_sha256"] or result.get("config_sha256") != bundle["config_sha256"]:
        raise ValueError("AnyJev L2 result does not match the frozen bundle/config")
    if result.get("numeric_prediction_in_state") is not False or result.get("selection_scheme") != "past_train_then_validation_then_later_test":
        raise ValueError("AnyJev L2 result violates the chronological separation contract")
    responses = result.get("predictions", [])
    if not responses or len({row["job_id"] for row in responses}) != len(responses):
        raise ValueError("AnyJev L2 predictions are empty or duplicated")
    for row in responses:
        if row.get("track") != "anyjev" or row.get("level") != "L2" or row.get("split") != "test":
            raise ValueError("Unexpected AnyJev L2 prediction provenance")
        if row.get("actual_not_in_request") is not True:
            raise ValueError("AnyJev L2 prediction does not attest outcome separation")
        if any(name in row for name in ("actual_return", "actual_direction", "forecast_return", "combined_score")):
            raise ValueError("AnyJev L2 prediction contains a forbidden outcome or numeric forecast")
    with duckdb.connect() as connection:
        numeric = connection.execute(
            "SELECT event_id,prediction_time,horizon_sessions,direction,probability_down,probability_flat,"
            "probability_up,actual_return,actual_direction FROM read_parquet(?)",
            [str(root()/parent["files"]["quantitative_predictions"])],
        ).fetchall()
    numeric_by_key = {(row[0], int(row[2])): row for row in numeric}
    joined = []
    for response in responses:
        key = (response["event_id"], int(response["horizon_sessions"]))
        if key not in numeric_by_key:
            raise ValueError(f"AnyJev L2 response has no frozen numeric peer: {key}")
        numeric_row = numeric_by_key[key]
        actual_direction = str(numeric_row[8])
        numeric_correct = str(numeric_row[3]) == actual_direction
        anyjev_correct = response["direction"] == actual_direction
        comparison = ("both_correct" if numeric_correct and anyjev_correct else
                      "quantitative_only" if numeric_correct else
                      "anyjev_only" if anyjev_correct else "both_wrong")
        probabilities = response["probabilities"]
        joined.append({
            "job_id": response["job_id"], "event_id": response["event_id"],
            "prediction_time": numeric_row[1], "horizon_sessions": int(response["horizon_sessions"]),
            "quantitative_direction": str(numeric_row[3]),
            "quantitative_probability_down": float(numeric_row[4]),
            "quantitative_probability_flat": float(numeric_row[5]),
            "quantitative_probability_up": float(numeric_row[6]),
            "anyjev_direction": response["direction"],
            "anyjev_probability_down": float(probabilities["down"]),
            "anyjev_probability_flat": float(probabilities["flat"]),
            "anyjev_probability_up": float(probabilities["up"]),
            "actual_return": float(numeric_row[7]), "actual_direction": actual_direction,
            "comparison": comparison,
        })
    counts = {name: sum(row["comparison"] == name for row in joined)
              for name in ("both_correct", "quantitative_only", "anyjev_only", "both_wrong")}
    numeric_accuracy = fmean(row["quantitative_direction"] == row["actual_direction"] for row in joined)
    anyjev_accuracy = fmean(row["anyjev_direction"] == row["actual_direction"] for row in joined)
    direction_counts = {name: sum(row["anyjev_direction"] == name for row in joined)
                        for name in ("down", "flat", "up")}
    constant_direction = sum(value > 0 for value in direction_counts.values()) == 1
    if constant_direction:
        verdict = "constant_prediction"
    elif anyjev_accuracy <= float(result["test_majority_class_accuracy"]):
        verdict = "does_not_beat_simple_majority_guess"
    else:
        verdict = "beats_simple_majority_guess_on_this_test"
    result_bytes = path.read_bytes()
    head_bytes = head_path.read_bytes() if head_path is not None else None
    identity = {"parent_dataset_id": parent["dataset_id"], "bundle_dataset_id": bundle["dataset_id"],
                "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
                "head_sha256": hashlib.sha256(head_bytes).hexdigest() if head_bytes is not None else None,
                "rows": joined}
    dataset_id = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str,
                                            separators=(",", ":")).encode()).hexdigest()[:20]
    folder = root()/"gold"/"communication_anyjev_l2"/dataset_id
    _write_parquet(joined, folder/"comparison.parquet", "horizon_sessions,prediction_time,event_id")
    (folder/"result.json").write_bytes(result_bytes)
    files = {"comparison": (folder/"comparison.parquet").relative_to(root()).as_posix(),
             "result": (folder/"result.json").relative_to(root()).as_posix()}
    if head_bytes is not None:
        (folder/"head.json").write_bytes(head_bytes)
        files["head"] = (folder/"head.json").relative_to(root()).as_posix()
    report = {
        "dataset_id": dataset_id, "parent_dataset_id": parent["dataset_id"],
        "bundle_dataset_id": bundle["dataset_id"], "rows": len(joined), "horizon_sessions": 5,
        "model_id": result["model_id"], "level": "L2", "split_rows": result["split_rows"],
        "counts": counts, "direction_counts": direction_counts,
        "quantitative_accuracy": numeric_accuracy, "anyjev_accuracy": anyjev_accuracy,
        "majority_guess_accuracy": float(result["test_majority_class_accuracy"]),
        "anyjev_log_loss": float(result["test_log_loss"]), "anyjev_brier": float(result["test_brier"]),
        "selected_lambda": float(result["selected_lambda"]),
        "selected_temperature": float(result["selected_temperature"]),
        "elapsed_seconds": float(result["elapsed_seconds"]), "verdict": verdict,
        "combined_score": False, "numeric_prediction_in_state": False,
        "selection_scheme": result["selection_scheme"],
        "result_sha256": identity["result_sha256"], "head_sha256": identity["head_sha256"],
        "files": files,
        "limitations": [
            "This test covers ECB/Eurosystem communication texts and a five-session horizon only.",
            "Historical full-text availability is non-strict, so this is research evidence rather than a live-trading claim.",
            "The numeric and AnyJev predictions are shown side by side and are never averaged.",
        ], "generated_at": datetime.now(UTC).isoformat(),
    }
    atomic_json(folder/"manifest.json", report)
    atomic_json(root()/"reports"/"communication_anyjev_l2.json", report)
    return report
