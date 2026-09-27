"""Pre-registered Elastic Net comparison on the fixed Tier A feature matrix."""
from __future__ import annotations

import hashlib
import json
import math
import os
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

import numpy as np
import yaml
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import ElasticNet
from sklearn.preprocessing import StandardScaler

from ..store import atomic_json, root
from .grouped_tier_a import _load_rows
from .pipeline import _correlation, _mse, _predict, _write_parquet
from .state_vector import _binom_two_sided, _bootstrap_mean_ci, _fit_variant, _fold_splits

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "elastic_net.yaml"


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-elastic-net-1":
        raise ValueError("Unsupported Elastic Net config version")
    required = {"parent_report", "features", "horizons", "ridge_lambdas", "elastic_net", "sample",
                "comparison", "multiplicity", "bootstrap"}
    if not required <= set(config):
        raise ValueError(f"Elastic Net config is missing keys: {sorted(required - set(config))}")
    if len(config["features"]) != len(set(config["features"])):
        raise ValueError("Elastic Net feature names must be unique")
    if sorted(config["horizons"]) != [1, 5, 20, 60] or config["multiplicity"]["family_size"] != 4:
        raise ValueError("Elastic Net family is fixed to four horizons")
    model = config["elastic_net"]
    if (not model.get("alphas") or any(float(value) <= 0 for value in model["alphas"])
            or not model.get("l1_ratios") or any(not 0 < float(value) <= 1 for value in model["l1_ratios"])):
        raise ValueError("Elastic Net alpha and L1 grids must be positive")
    if model.get("selection") != "cyclic" or model.get("tie_break") != "stronger_regularization":
        raise ValueError("Elastic Net v1 requires deterministic selection and a fixed tie-break")
    if not config["sample"].get("no_imputation"):
        raise ValueError("Elastic Net cannot impute missing observations")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def fit_elastic_net(rows: list[dict], features: list[str], target: str, alpha: float, l1_ratio: float,
                    max_iter: int, tolerance: float) -> dict:
    if not rows:
        raise ValueError("Cannot fit Elastic Net on an empty sample")
    matrix = np.asarray([[float(row[name]) for name in features] for row in rows], dtype=float)
    outcome = np.asarray([float(row[target]) for row in rows], dtype=float)
    scaler = StandardScaler()
    standardized = scaler.fit_transform(matrix)
    estimator = ElasticNet(alpha=float(alpha), l1_ratio=float(l1_ratio), fit_intercept=True,
                           max_iter=int(max_iter), tol=float(tolerance), selection="cyclic")
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        estimator.fit(standardized, outcome)
    coefficients = [float(value) for value in estimator.coef_]
    return {"scaler": scaler, "estimator": estimator, "coefficients": coefficients,
            "nonzero_features": [name for name, value in zip(features, coefficients) if abs(value) > 1e-12],
            "alpha": float(alpha), "l1_ratio": float(l1_ratio)}


def predict_elastic_net(model: dict, rows: list[dict], features: list[str]) -> list[float]:
    matrix = np.asarray([[float(row[name]) for name in features] for row in rows], dtype=float)
    return [float(value) for value in model["estimator"].predict(model["scaler"].transform(matrix))]


def _select_parameters(train: list[dict], validation: list[dict], features: list[str], target: str,
                       settings: dict) -> tuple[float, float]:
    actual = [float(row[target]) for row in validation]
    best = None
    for alpha in settings["alphas"]:
        for l1_ratio in settings["l1_ratios"]:
            model = fit_elastic_net(train, features, target, float(alpha), float(l1_ratio),
                                    int(settings["max_iter"]), float(settings["tolerance"]))
            score = _mse(actual, predict_elastic_net(model, validation, features))
            candidate = (score, -float(alpha), -float(l1_ratio), float(alpha), float(l1_ratio))
            if best is None or candidate < best:
                best = candidate
    return best[3], best[4]


def score_elastic_net(rows: list[dict], features: list[str], horizons: list[int], ridge_lambdas: list[float],
                      settings: dict, minimums: tuple[int, int, int], bootstrap: dict) -> dict:
    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    result = {}
    for horizon in horizons:
        target = f"target_return_{horizon}d"
        folds, actual_all, baseline_all, treatment_all, mean_all = [], [], [], [], []
        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
                continue
            baseline_model, baseline_lambda = _fit_variant(split, features, ridge_lambdas)
            selected_alpha, selected_ratio = _select_parameters(
                split["selection_train"], split["validation"], features, target, settings)
            treatment_model = fit_elastic_net(
                split["fit_rows"], features, target, selected_alpha, selected_ratio,
                int(settings["max_iter"]), float(settings["tolerance"]))
            actual = [float(row[target]) for row in split["test_rows"]]
            baseline_pred = [_predict(baseline_model, row, features) for row in split["test_rows"]]
            treatment_pred = predict_elastic_net(treatment_model, split["test_rows"], features)
            historical_mean = fmean(float(row[target]) for row in split["fit_rows"])
            baseline_mse, treatment_mse = _mse(actual, baseline_pred), _mse(actual, treatment_pred)
            folds.append({"fold_id": f"{test_year}-{horizon}d", "horizon_sessions": horizon,
                          "test_year": test_year, "test_rows": len(actual),
                          "baseline_lambda": baseline_lambda, "selected_alpha": selected_alpha,
                          "selected_l1_ratio": selected_ratio, "baseline_mse": baseline_mse,
                          "treatment_mse": treatment_mse, "improvement": baseline_mse - treatment_mse,
                          "nonzero_count": len(treatment_model["nonzero_features"]),
                          "selected_features": treatment_model["nonzero_features"]})
            actual_all.extend(actual); baseline_all.extend(baseline_pred); treatment_all.extend(treatment_pred)
            mean_all.extend([historical_mean] * len(actual))
        if not folds:
            raise ValueError(f"No valid Elastic Net folds for horizon {horizon}")
        improvements = [fold["improvement"] for fold in folds]
        k = sum(value > 0 for value in improvements)
        mean, lower, upper = _bootstrap_mean_ci(improvements, int(bootstrap["reps"]), int(bootstrap["seed"]))
        p_value = _binom_two_sided(k, len(folds))
        baseline_mse, treatment_mse, mean_mse = (_mse(actual_all, baseline_all),
                                                  _mse(actual_all, treatment_all), _mse(actual_all, mean_all))
        selection_counts = Counter(feature for fold in folds for feature in fold["selected_features"])
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
            "selection": {"mean_nonzero_features": fmean(fold["nonzero_count"] for fold in folds),
                          "feature_frequency": {name: selection_counts[name] / len(folds) for name in features},
                          "parameter_frequency": dict(Counter(
                              f"alpha={fold['selected_alpha']:g},l1={fold['selected_l1_ratio']:g}" for fold in folds))},
        }
    return result


def build_elastic_net(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    rows, parent = _load_rows(config)
    result = score_elastic_net(rows, list(config["features"]), list(config["horizons"]),
                               [float(value) for value in config["ridge_lambdas"]], config["elastic_net"],
                               (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]),
                                int(config["minimum_test_rows"])), config["bootstrap"])
    dataset_id = hashlib.sha256(f"{config_hash}:{parent['dataset_id']}".encode()).hexdigest()[:20]
    folder = root() / "gold" / "denn_elastic_net" / dataset_id
    fold_rows = []
    for data in result.values():
        for fold in data["folds"]:
            row = dict(fold)
            row["selected_features"] = ",".join(row["selected_features"])
            fold_rows.append(row)
    _write_parquet(fold_rows, folder / "fold_metrics.parquet", "horizon_sessions,test_year")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "generated_at": datetime.now(UTC).isoformat(),
        "input_dataset_id": parent["dataset_id"], "config_sha256": config_hash,
        "sample_rows": len(rows), "date_from": str(rows[0]["feature_date"]), "date_to": str(rows[-1]["feature_date"]),
        "feature_names": list(config["features"]), "horizons": list(config["horizons"]),
        "sample": json.loads(json.dumps(config["sample"], default=str)), "elastic_net": config["elastic_net"],
        "comparison": config["comparison"], "multiplicity": config["multiplicity"],
        "result": {label: {key: value for key, value in data.items() if key != "folds"}
                   for label, data in result.items()},
        "files": {"fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix()},
        "strict_pit_eligible": False, "inference_status": config["inference_status"],
        "limitations": [
            "The data are current-history rather than proven historical versions.",
            "The model chooses settings only from the preceding validation year; test years remain unseen.",
            "A feature being kept by the model does not prove that it causes EUR/USD movement.",
            "The result describes forecast error on this dataset and is not a trading strategy.",
        ],
    }
    atomic_json(root() / "reports" / "denn_elastic_net.json", report)
    return report
