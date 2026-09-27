"""Pre-registered fold-local PCA compression of economic feature blocks."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean

import yaml

from ..store import atomic_json, root
from .grouped_tier_a import _load_rows
from .pipeline import _correlation, _fit_ridge, _mse, _predict, _write_parquet
from .state_vector import _binom_two_sided, _bootstrap_mean_ci, _fit_variant, _fold_splits

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "block_pca.yaml"


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-block-pca-1":
        raise ValueError("Unsupported block-PCA config version")
    required = {"parent_report", "features", "blocks", "horizons", "ridge_lambdas", "sample",
                "compression", "comparison", "multiplicity", "bootstrap"}
    if not required <= set(config):
        raise ValueError(f"Block-PCA config is missing keys: {sorted(required - set(config))}")
    members = [member for block in config["blocks"] for member in block["members"]]
    if (len({block["name"] for block in config["blocks"]}) != len(config["blocks"])
            or sorted(members) != sorted(config["features"])):
        raise ValueError("Block-PCA blocks must uniquely partition the feature set")
    if sorted(config["horizons"]) != [1, 5, 20, 60] or config["multiplicity"]["family_size"] != 4:
        raise ValueError("Block-PCA family is fixed to four horizons")
    if config["compression"].get("components_per_block") != 1:
        raise ValueError("Block-PCA v1 requires exactly one component per block")
    if int(config["compression"].get("power_iterations", 0)) < 20:
        raise ValueError("Block-PCA requires at least 20 deterministic power iterations")
    if not config["sample"].get("no_imputation"):
        raise ValueError("Block-PCA cannot impute missing observations")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _first_component(rows: list[dict], members: list[str], iterations: int) -> dict:
    means = [fmean(float(row[name]) for row in rows) for name in members]
    scales = []
    for index, name in enumerate(members):
        variance = fmean((float(row[name]) - means[index]) ** 2 for row in rows)
        scales.append(math.sqrt(variance) if variance > 1e-18 else 1.0)
    width = len(members)
    covariance = [[0.0] * width for _ in range(width)]
    for row in rows:
        values = [(float(row[name]) - means[index]) / scales[index] for index, name in enumerate(members)]
        for left in range(width):
            for right in range(width):
                covariance[left][right] += values[left] * values[right] / len(rows)
    vector = [1.0 / math.sqrt(width)] * width
    for _ in range(iterations):
        update = [sum(covariance[row][column] * vector[column] for column in range(width))
                  for row in range(width)]
        norm = math.sqrt(sum(value * value for value in update))
        if norm <= 1e-18:
            vector = [1.0] + [0.0] * (width - 1)
            break
        vector = [value / norm for value in update]
    anchor = sum(vector)
    if abs(anchor) <= 1e-12:
        anchor = vector[max(range(width), key=lambda index: abs(vector[index]))]
    if anchor < 0:
        vector = [-value for value in vector]
    eigenvalue = sum(vector[left] * covariance[left][right] * vector[right]
                     for left in range(width) for right in range(width))
    trace = sum(covariance[index][index] for index in range(width))
    return {"members": members, "means": means, "scales": scales, "loadings": vector,
            "explained_variance_share": 0.0 if trace <= 1e-18 else eigenvalue / trace}


def fit_block_pca(rows: list[dict], blocks: list[dict], iterations: int = 100) -> dict:
    if not rows:
        raise ValueError("Cannot fit block PCA on an empty sample")
    return {block["name"]: _first_component(rows, list(block["members"]), iterations) for block in blocks}


def transform_block_states(rows: list[dict], models: dict) -> list[dict]:
    transformed = []
    for row in rows:
        item = dict(row)
        for name, model in models.items():
            values = [(float(row[member]) - model["means"][index]) / model["scales"][index]
                      for index, member in enumerate(model["members"])]
            item[f"state__{name}"] = sum(weight * value for weight, value in zip(model["loadings"], values))
        transformed.append(item)
    return transformed


def _select_lambda(train: list[dict], validation: list[dict], columns: list[str], target: str,
                   lambdas: list[float]) -> float:
    best_lambda, best_score = None, math.inf
    for ridge in lambdas:
        model = _fit_ridge(train, columns, target, float(ridge))
        score = _mse([float(row[target]) for row in validation],
                     [_predict(model, row, columns) for row in validation])
        if score < best_score:
            best_lambda, best_score = float(ridge), score
    return float(best_lambda)


def score_block_pca(rows: list[dict], features: list[str], blocks: list[dict], horizons: list[int],
                    lambdas: list[float], minimums: tuple[int, int, int], bootstrap: dict,
                    iterations: int = 100) -> dict:
    state_names = [f"state__{block['name']}" for block in blocks]
    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    result = {}
    for horizon in horizons:
        target = f"target_return_{horizon}d"
        folds, actual_all, baseline_all, treatment_all, mean_all = [], [], [], [], []
        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
                continue
            baseline_model, baseline_lambda = _fit_variant(split, features, lambdas)
            selection_pca = fit_block_pca(split["selection_train"], blocks, iterations)
            selection = transform_block_states(split["selection_train"], selection_pca)
            validation = transform_block_states(split["validation"], selection_pca)
            treatment_lambda = _select_lambda(selection, validation, state_names, target, lambdas)
            fit_pca = fit_block_pca(split["fit_rows"], blocks, iterations)
            fit_rows = transform_block_states(split["fit_rows"], fit_pca)
            test_rows = transform_block_states(split["test_rows"], fit_pca)
            treatment_model = _fit_ridge(fit_rows, state_names, target, treatment_lambda)
            actual = [float(row[target]) for row in split["test_rows"]]
            baseline_pred = [_predict(baseline_model, row, features) for row in split["test_rows"]]
            treatment_pred = [_predict(treatment_model, row, state_names) for row in test_rows]
            historical_mean = fmean(float(row[target]) for row in split["fit_rows"])
            baseline_mse, treatment_mse = _mse(actual, baseline_pred), _mse(actual, treatment_pred)
            folds.append({"fold_id": f"{test_year}-{horizon}d", "horizon_sessions": horizon,
                          "test_year": test_year, "test_rows": len(actual),
                          "baseline_lambda": baseline_lambda, "treatment_lambda": treatment_lambda,
                          "baseline_mse": baseline_mse, "treatment_mse": treatment_mse,
                          "improvement": baseline_mse - treatment_mse,
                          "explained_variance": {name: model["explained_variance_share"]
                                                 for name, model in fit_pca.items()}})
            actual_all.extend(actual); baseline_all.extend(baseline_pred); treatment_all.extend(treatment_pred)
            mean_all.extend([historical_mean] * len(actual))
        if not folds:
            raise ValueError(f"No valid block-PCA folds for horizon {horizon}")
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
            "mean_explained_variance": {block["name"]: fmean(
                fold["explained_variance"][block["name"]] for fold in folds) for block in blocks},
        }
    return result


def build_block_pca(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    rows, parent = _load_rows(config)
    result = score_block_pca(rows, list(config["features"]), config["blocks"], list(config["horizons"]),
                             [float(value) for value in config["ridge_lambdas"]],
                             (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]),
                              int(config["minimum_test_rows"])), config["bootstrap"],
                             int(config["compression"]["power_iterations"]))
    dataset_id = hashlib.sha256(f"{config_hash}:{parent['dataset_id']}".encode()).hexdigest()[:20]
    folder = root() / "gold" / "denn_block_pca" / dataset_id
    fold_rows = []
    for data in result.values():
        for fold in data["folds"]:
            row = {key: value for key, value in fold.items() if key != "explained_variance"}
            row.update({f"explained__{name}": value for name, value in fold["explained_variance"].items()})
            fold_rows.append(row)
    _write_parquet(fold_rows, folder / "fold_metrics.parquet", "horizon_sessions,test_year")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "generated_at": datetime.now(UTC).isoformat(),
        "input_dataset_id": parent["dataset_id"], "config_sha256": config_hash,
        "sample_rows": len(rows), "date_from": str(rows[0]["feature_date"]), "date_to": str(rows[-1]["feature_date"]),
        "feature_names": list(config["features"]), "blocks": config["blocks"],
        "state_names": [f"state__{block['name']}" for block in config["blocks"]],
        "horizons": list(config["horizons"]), "sample": json.loads(json.dumps(config["sample"], default=str)),
        "compression": config["compression"], "comparison": config["comparison"],
        "multiplicity": config["multiplicity"], "bootstrap": config["bootstrap"],
        "result": {label: {key: value for key, value in data.items() if key != "folds"}
                   for label, data in result.items()},
        "files": {"fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix()},
        "strict_pit_eligible": False, "inference_status": config["inference_status"],
        "limitations": [
            "All inputs are current-history and non-strict; historical vintages are not proven.",
            "Each block is compressed to one unsupervised linear component, which can discard predictive directions.",
            "PCA means, scales and loadings are fit separately inside each past-only walk-forward fold.",
            "Only paired four-horizon improvement is in the formal family; explained variance is diagnostic.",
        ],
    }
    atomic_json(root() / "reports" / "denn_block_pca.json", report)
    return report
