"""Pre-registered nonlinear comparison on the fixed Tier A feature matrix."""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

import numpy as np
import yaml
from sklearn.ensemble import HistGradientBoostingRegressor

from ..store import atomic_json, root
from .elastic_net import (
    _load_config as _load_elastic_config,
    _select_parameters as _select_elastic_parameters,
    fit_elastic_net,
    predict_elastic_net,
)
from .grouped_tier_a import _load_rows
from .pipeline import _correlation, _mse, _write_parquet
from .state_vector import _binom_two_sided, _bootstrap_mean_ci, _fold_splits

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "nonlinear_boosting.yaml"


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-nonlinear-boosting-1":
        raise ValueError("Unsupported nonlinear boosting config version")
    required = {"parent_report", "baseline_report", "baseline_config", "features", "horizons",
                "gradient_boosting", "sample", "comparison", "multiplicity", "bootstrap"}
    if not required <= set(config):
        raise ValueError(f"Nonlinear config is missing keys: {sorted(required - set(config))}")
    if len(config["features"]) != len(set(config["features"])):
        raise ValueError("Nonlinear feature names must be unique")
    if sorted(config["horizons"]) != [1, 5, 20, 60] or config["multiplicity"]["family_size"] != 4:
        raise ValueError("Nonlinear comparison is fixed to four horizons")
    model = config["gradient_boosting"]
    if model.get("implementation") != "sklearn_hist_gradient_boosting":
        raise ValueError("Nonlinear v1 requires the registered sklearn implementation")
    if model.get("loss") != "squared_error" or model.get("early_stopping") is not False:
        raise ValueError("Nonlinear v1 requires squared error and deterministic full fitting")
    if (not model.get("learning_rates") or any(float(value) <= 0 for value in model["learning_rates"])
            or not model.get("max_leaf_nodes") or any(int(value) < 2 for value in model["max_leaf_nodes"])
            or not model.get("l2_regularizations") or any(float(value) < 0 for value in model["l2_regularizations"])):
        raise ValueError("Nonlinear parameter grids must contain valid positive values")
    if model.get("tie_break") != "simpler_model" or not config["sample"].get("no_imputation"):
        raise ValueError("Nonlinear v1 requires the fixed tie-break and no imputation")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def fit_boosting(rows: list[dict], features: list[str], target: str, settings: dict) -> HistGradientBoostingRegressor:
    if not rows:
        raise ValueError("Cannot fit nonlinear model on an empty sample")
    matrix = np.asarray([[float(row[name]) for name in features] for row in rows], dtype=float)
    outcome = np.asarray([float(row[target]) for row in rows], dtype=float)
    model = HistGradientBoostingRegressor(
        loss=settings["loss"], learning_rate=float(settings["learning_rate"]),
        max_iter=int(settings["max_iter"]), max_leaf_nodes=int(settings["max_leaf_nodes"]),
        min_samples_leaf=int(settings["min_samples_leaf"]),
        l2_regularization=float(settings["l2_regularization"]), max_bins=int(settings["max_bins"]),
        early_stopping=bool(settings["early_stopping"]), random_state=int(settings["random_state"]),
    )
    model.fit(matrix, outcome)
    return model


def predict_boosting(model: HistGradientBoostingRegressor, rows: list[dict], features: list[str]) -> list[float]:
    matrix = np.asarray([[float(row[name]) for name in features] for row in rows], dtype=float)
    return [float(value) for value in model.predict(matrix)]


def _settings(grid: dict, learning_rate: float, max_leaf_nodes: int, l2: float) -> dict:
    return {
        "loss": grid["loss"], "learning_rate": float(learning_rate),
        "max_leaf_nodes": int(max_leaf_nodes), "l2_regularization": float(l2),
        "max_iter": int(grid["max_iter"]), "min_samples_leaf": int(grid["min_samples_leaf"]),
        "max_bins": int(grid["max_bins"]), "early_stopping": bool(grid["early_stopping"]),
        "random_state": int(grid["random_state"]),
    }


def _select_boosting_parameters(train: list[dict], validation: list[dict], features: list[str], target: str,
                                 grid: dict) -> dict:
    actual = [float(row[target]) for row in validation]
    best = None
    for learning_rate in grid["learning_rates"]:
        for leaves in grid["max_leaf_nodes"]:
            for l2 in grid["l2_regularizations"]:
                settings = _settings(grid, float(learning_rate), int(leaves), float(l2))
                model = fit_boosting(train, features, target, settings)
                score = _mse(actual, predict_boosting(model, validation, features))
                candidate = (score, int(leaves), float(learning_rate), -float(l2), settings)
                if best is None or candidate[:-1] < best[:-1]:
                    best = candidate
    return best[-1]


def score_nonlinear(rows: list[dict], features: list[str], horizons: list[int], elastic_settings: dict,
                    boost_grid: dict, minimums: tuple[int, int, int], bootstrap: dict) -> dict:
    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    result = {}
    for horizon in horizons:
        target = f"target_return_{horizon}d"
        folds, actual_all, baseline_all, treatment_all, mean_all = [], [], [], [], []
        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
                continue
            alpha, ratio = _select_elastic_parameters(
                split["selection_train"], split["validation"], features, target, elastic_settings)
            baseline_model = fit_elastic_net(
                split["fit_rows"], features, target, alpha, ratio,
                int(elastic_settings["max_iter"]), float(elastic_settings["tolerance"]))
            chosen = _select_boosting_parameters(
                split["selection_train"], split["validation"], features, target, boost_grid)
            treatment_model = fit_boosting(split["fit_rows"], features, target, chosen)
            actual = [float(row[target]) for row in split["test_rows"]]
            baseline_pred = predict_elastic_net(baseline_model, split["test_rows"], features)
            treatment_pred = predict_boosting(treatment_model, split["test_rows"], features)
            historical_mean = fmean(float(row[target]) for row in split["fit_rows"])
            baseline_mse, treatment_mse = _mse(actual, baseline_pred), _mse(actual, treatment_pred)
            folds.append({
                "fold_id": f"{test_year}-{horizon}d", "horizon_sessions": horizon,
                "test_year": test_year, "test_rows": len(actual), "baseline_alpha": alpha,
                "baseline_l1_ratio": ratio, "learning_rate": chosen["learning_rate"],
                "max_leaf_nodes": chosen["max_leaf_nodes"], "l2_regularization": chosen["l2_regularization"],
                "baseline_mse": baseline_mse, "treatment_mse": treatment_mse,
                "improvement": baseline_mse - treatment_mse,
            })
            actual_all.extend(actual); baseline_all.extend(baseline_pred); treatment_all.extend(treatment_pred)
            mean_all.extend([historical_mean] * len(actual))
        if not folds:
            raise ValueError(f"No valid nonlinear folds for horizon {horizon}")
        improvements = [fold["improvement"] for fold in folds]
        k = sum(value > 0 for value in improvements)
        mean, lower, upper = _bootstrap_mean_ci(improvements, int(bootstrap["reps"]), int(bootstrap["seed"]))
        p_value = _binom_two_sided(k, len(folds))
        baseline_mse, treatment_mse, mean_mse = (_mse(actual_all, baseline_all),
                                                  _mse(actual_all, treatment_all), _mse(actual_all, mean_all))
        choices = Counter(
            f"lr={fold['learning_rate']:g},leaves={fold['max_leaf_nodes']},l2={fold['l2_regularization']:g}"
            for fold in folds)
        result[f"{horizon}d"] = {
            "folds": folds,
            "baseline": {"mse": baseline_mse, "skill_vs_mean": 1 - baseline_mse / mean_mse,
                         "correlation": _correlation(actual_all, baseline_all)},
            "treatment": {"mse": treatment_mse, "skill_vs_mean": 1 - treatment_mse / mean_mse,
                          "correlation": _correlation(actual_all, treatment_all)},
            "paired_improvement": {"mean": mean, "bootstrap_ci": [lower, upper],
                                   "folds_in_favor": k, "folds": len(folds), "sign_test_p": p_value,
                                   "decision_threshold": 0.05 / len(horizons),
                                   "passed": p_value < 0.05 / len(horizons)},
            "parameter_frequency": dict(choices),
        }
    return result


def build_nonlinear_boosting(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    project = Path(os.environ.get("FXLAB_PROJECT", "."))
    elastic_config, elastic_hash = _load_elastic_config(project / config["baseline_config"])
    if list(config["features"]) != list(elastic_config["features"]) or list(config["horizons"]) != list(elastic_config["horizons"]):
        raise ValueError("Nonlinear and Elastic Net comparisons must use identical features and horizons")
    rows, parent = _load_rows(config)
    result = score_nonlinear(
        rows, list(config["features"]), list(config["horizons"]), elastic_config["elastic_net"],
        config["gradient_boosting"],
        (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]),
         int(config["minimum_test_rows"])), config["bootstrap"],
    )
    dataset_id = hashlib.sha256(f"{config_hash}:{elastic_hash}:{parent['dataset_id']}".encode()).hexdigest()[:20]
    folder = root() / "gold" / "denn_nonlinear_boosting" / dataset_id
    fold_rows = [fold for data in result.values() for fold in data["folds"]]
    _write_parquet(fold_rows, folder / "fold_metrics.parquet", "horizon_sessions,test_year")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "generated_at": datetime.now(UTC).isoformat(),
        "input_dataset_id": parent["dataset_id"], "config_sha256": config_hash,
        "baseline_config_sha256": elastic_hash, "baseline_report": config["baseline_report"],
        "sample_rows": len(rows), "date_from": str(rows[0]["feature_date"]), "date_to": str(rows[-1]["feature_date"]),
        "feature_names": list(config["features"]), "horizons": list(config["horizons"]),
        "sample": json.loads(json.dumps(config["sample"], default=str)),
        "gradient_boosting": config["gradient_boosting"], "comparison": config["comparison"],
        "multiplicity": config["multiplicity"],
        "result": {label: {key: value for key, value in data.items() if key != "folds"}
                   for label, data in result.items()},
        "files": {"fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix()},
        "strict_pit_eligible": False, "inference_status": config["inference_status"],
        "limitations": [
            "The data are current-history rather than proven historical versions.",
            "Both models choose settings only from the preceding validation year; test years remain unseen.",
            "A lower forecasting error does not prove that any input causes EUR/USD movement.",
            "The result describes this fixed dataset and is not a trading strategy.",
        ],
    }
    atomic_json(root() / "reports" / "denn_nonlinear_boosting.json", report)
    return report
