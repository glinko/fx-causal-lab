"""Pre-registered deterministic memory and freshness-decay ablation."""
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
from .pipeline import _correlation, _mse, _predict, _write_parquet, age_decay
from .state_vector import _binom_two_sided, _bootstrap_mean_ci, _fit_variant, _fold_splits

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "memory_ablation.yaml"


def _json_safe(value):
    """Convert YAML-native dates in registered metadata to stable JSON strings."""
    return json.loads(json.dumps(value, default=str))


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-memory-ablation-1":
        raise ValueError("Unsupported memory-ablation config version")
    required = {"parent_report", "baseline_features", "direct_memory_features", "decayed_features",
                "horizons", "ridge_lambdas", "sample", "comparison", "multiplicity", "bootstrap"}
    if not required <= set(config):
        raise ValueError(f"Memory config is missing keys: {sorted(required - set(config))}")
    if sorted(config["horizons"]) != [1, 5, 20, 60] or config["multiplicity"]["family_size"] != 4:
        raise ValueError("Memory family is fixed to four horizons")
    derived = [item["name"] for item in config["decayed_features"]]
    baseline = list(config["baseline_features"])
    direct = list(config["direct_memory_features"])
    if len(baseline) != len(set(baseline)) or len(direct) != len(set(direct)):
        raise ValueError("Baseline and direct-memory feature names must be unique")
    if set(baseline) & set(direct):
        raise ValueError("Direct-memory features must be separate from baseline features")
    if (len(derived) != len(set(derived)) or
            set(derived) & (set(baseline) | set(direct))):
        raise ValueError("Memory feature names must be unique and separate from baseline features")
    for item in config["decayed_features"]:
        if not {"name", "value", "age", "half_life_days"} <= set(item):
            raise ValueError("Each decayed feature requires name, value, age and half_life_days")
        if item["value"] not in baseline:
            raise ValueError("Decay values must reference a registered baseline feature")
    if any(float(item["half_life_days"]) <= 0 for item in config["decayed_features"]):
        raise ValueError("Decay half-lives must be positive")
    if not config["sample"].get("no_imputation"):
        raise ValueError("Memory ablation cannot impute missing observations")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _load_rows(config: dict) -> tuple[list[dict], dict, list[str]]:
    report_path = root() / "reports" / f"{config['parent_report']}.json"
    if not report_path.exists():
        raise ValueError("tier_a_features.json is missing; run tier-a-features first")
    parent = json.loads(report_path.read_text(encoding="utf-8"))
    direct = list(config["direct_memory_features"])
    decay_inputs = []
    for spec in config["decayed_features"]:
        decay_inputs.extend([spec["value"], spec["age"]])
    inputs = list(dict.fromkeys(list(config["baseline_features"]) + direct + decay_inputs))
    columns = ["feature_date"] + inputs
    for horizon in config["horizons"]:
        columns += [f"target_return_{horizon}d", f"target_end_{horizon}d"]
    quoted = ",".join(f'"{column}"' for column in columns)
    with duckdb.connect() as connection:
        raw = connection.execute(f"SELECT {quoted} FROM read_parquet(?) ORDER BY feature_date",
                                 [str(root() / parent["files"]["features"])]).fetchall()
    rows = []
    for values in raw:
        if any(value is None for value in values[1:]):
            continue
        item = {"feature_date": date.fromisoformat(str(values[0]))}
        offset = 1
        for name in inputs:
            value = float(values[offset]); offset += 1
            if not math.isfinite(value):
                raise ValueError(f"Non-finite memory input: {name}")
            item[name] = value
        for spec in config["decayed_features"]:
            age = item[spec["age"]]
            if age < 0:
                raise ValueError(f"Negative feature age: {spec['age']}")
            item[spec["name"]] = item[spec["value"]] * age_decay(age, float(spec["half_life_days"]))
        for horizon in config["horizons"]:
            target = float(values[offset]); target_end = values[offset + 1]; offset += 2
            if not math.isfinite(target):
                raise ValueError(f"Non-finite target_return_{horizon}d")
            item[f"target_return_{horizon}d"] = target
            item[f"target_end_{horizon}d"] = date.fromisoformat(str(target_end))
        rows.append(item)
    if len(rows) < 2500:
        raise ValueError("Memory complete-case matrix is too short")
    expected = date.fromisoformat(str(config["sample"]["expected_start_not_before"]))
    if rows[0]["feature_date"] < expected:
        raise ValueError("Memory interval begins before the registered source boundary")
    treatment = direct + [item["name"] for item in config["decayed_features"]]
    return rows, parent, treatment


def score_memory(rows: list[dict], baseline_features: list[str], memory_features: list[str],
                 horizons: list[int], ridge_lambdas: list[float], minimums: tuple[int, int, int],
                 bootstrap: dict) -> dict:
    """Compare observed state with observed state plus fixed memory features."""
    result = {}
    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    treatment_features = baseline_features + memory_features
    for horizon in horizons:
        target = f"target_return_{horizon}d"
        folds = []
        actual_all, baseline_all, treatment_all, mean_all = [], [], [], []
        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
                continue
            baseline_model, baseline_lambda = _fit_variant(split, baseline_features, ridge_lambdas)
            treatment_model, treatment_lambda = _fit_variant(split, treatment_features, ridge_lambdas)
            actual = [float(row[target]) for row in split["test_rows"]]
            baseline_pred = [_predict(baseline_model, row, baseline_features) for row in split["test_rows"]]
            treatment_pred = [_predict(treatment_model, row, treatment_features) for row in split["test_rows"]]
            historical_mean = fmean(float(row[target]) for row in split["fit_rows"])
            baseline_mse, treatment_mse = _mse(actual, baseline_pred), _mse(actual, treatment_pred)
            folds.append({"fold_id": f"{test_year}-{horizon}d", "horizon_sessions": horizon,
                          "test_year": test_year, "test_rows": len(actual),
                          "baseline_lambda": baseline_lambda, "treatment_lambda": treatment_lambda,
                          "baseline_mse": baseline_mse, "treatment_mse": treatment_mse,
                          "improvement": baseline_mse - treatment_mse})
            actual_all.extend(actual); baseline_all.extend(baseline_pred); treatment_all.extend(treatment_pred)
            mean_all.extend([historical_mean] * len(actual))
        if not folds:
            raise ValueError(f"No valid memory folds for horizon {horizon}")
        improvements = [fold["improvement"] for fold in folds]
        k = sum(value > 0 for value in improvements)
        mean, lower, upper = _bootstrap_mean_ci(improvements, int(bootstrap["reps"]), int(bootstrap["seed"]))
        p_value = _binom_two_sided(k, len(folds))
        baseline_mse, treatment_mse, mean_mse = (_mse(actual_all, baseline_all),
                                                  _mse(actual_all, treatment_all), _mse(actual_all, mean_all))
        result[f"{horizon}d"] = {
            "folds": folds,
            "baseline": {"mse": baseline_mse, "skill_vs_mean": 1 - baseline_mse / mean_mse,
                         "correlation": _correlation(actual_all, baseline_all)},
            "treatment": {"mse": treatment_mse, "skill_vs_mean": 1 - treatment_mse / mean_mse,
                          "correlation": _correlation(actual_all, treatment_all)},
            "paired_improvement": {"mean": mean, "bootstrap_ci": [lower, upper],
                                   "folds_in_favor": k, "folds": len(folds), "sign_test_p": p_value,
                                   "bonferroni_threshold": 0.05 / len(horizons),
                                   "significant": p_value < 0.05 / len(horizons)},
        }
    return result


def build_memory_ablation(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    rows, parent, memory_features = _load_rows(config)
    result = score_memory(rows, list(config["baseline_features"]), memory_features,
                          list(config["horizons"]), [float(v) for v in config["ridge_lambdas"]],
                          (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]),
                           int(config["minimum_test_rows"])), config["bootstrap"])
    dataset_id = hashlib.sha256(f"{config_hash}:{parent['dataset_id']}".encode()).hexdigest()[:20]
    folder = root() / "gold" / "denn_memory_ablation" / dataset_id
    fold_rows = [row for horizon in result.values() for row in horizon["folds"]]
    _write_parquet(fold_rows, folder / "fold_metrics.parquet", "horizon_sessions,test_year")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "generated_at": datetime.now(UTC).isoformat(),
        "input_dataset_id": parent["dataset_id"], "config_sha256": config_hash,
        "sample_rows": len(rows), "date_from": str(rows[0]["feature_date"]), "date_to": str(rows[-1]["feature_date"]),
        "baseline_features": list(config["baseline_features"]), "memory_features": memory_features,
        "decayed_features": config["decayed_features"], "horizons": list(config["horizons"]),
        "sample": _json_safe(config["sample"]), "bootstrap": _json_safe(config["bootstrap"]),
        "comparison": config["comparison"], "multiplicity": config["multiplicity"],
        "result": {label: {key: value for key, value in data.items() if key != "folds"}
                   for label, data in result.items()},
        "files": {"fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix()},
        "strict_pit_eligible": False, "inference_status": config["inference_status"],
        "limitations": [
            "All inputs are current-history and non-strict; historical vintages are not proven.",
            "Half-lives are fixed priors, not estimates of causal persistence.",
            "The treatment adds 13 columns and may overfit despite ridge selection.",
            "Only paired walk-forward improvement is in the formal four-horizon family.",
        ],
    }
    atomic_json(root() / "reports" / "denn_memory_ablation.json", report)
    return report
