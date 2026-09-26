"""DENN state-vector suite (v0.17, pre-registered protocol ``denn-state-vector-1``).

Scores the v0.14 gold feature matrix under the joint-state protocol frozen in
``config/state_vector.yaml``.

Methodological premise (DECISIONS.md, 2026-09-25): EUR/USD is an emergent
multivariate outcome; the unit of analysis is the economic state vector, not
an isolated factor. Univariate diagnostics are diagnostics ONLY and are never
a feature-selection gate.

Layers (per horizon)
--------------------
1. marginal        univariate OOS correlations — diagnostic only;
2. baseline        6-feature ridge walk-forward, logic identical to
                   ``pipeline._walk_forward`` (consistency anchor: must
                   reproduce the parent report exactly);
3. interactions    five theory-motivated product terms — single-term models
                   and one joint model;
4. ablation        leave-one-factor-out of the baseline — non-redundant
                   conditional contribution (positive delta-MSE = removing the
                   factor degrades the joint model);
5. regimes         baseline fit inside pre-registered z-score regimes —
                   conditional coefficients per regime level.

Inference: paired fold delta-MSE versus the baseline with an exact two-sided
binomial sign test plus a paired bootstrap CI on the mean delta; Bonferroni
multiplicity over the pre-registered family (6 LOO + 5 single terms + 1 joint
= 12). Pooled permutation importance of the joint model is diagnostic only.

Pure Python (stdlib + duckdb + yaml) — the runtime image has no numpy/scipy.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean

import duckdb
import yaml

from ..store import atomic_json, root
from .pipeline import _correlation, _fit_ridge, _load_config as _load_parent_config, _mse, _predict, _write_parquet

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "state_vector.yaml"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-state-vector-1":
        raise ValueError("Unsupported state-vector config version")
    required = {"parent_report", "features", "horizons", "ridge_lambdas",
                "minimum_train_rows", "minimum_validation_rows", "minimum_test_rows",
                "interactions", "regimes", "regime_minimum_rows", "ablation",
                "multiplicity", "sign_test", "bootstrap", "permutation_importance",
                "reporting", "strict_pit_eligible", "inference_status"}
    if not required <= set(config):
        raise ValueError(f"state-vector config missing keys: {sorted(required - set(config))}")
    features = list(config["features"])
    if len(features) != len(set(features)):
        raise ValueError("Duplicate feature names")
    if not config["horizons"]:
        raise ValueError("At least one horizon is required")
    for term in config["interactions"]:
        if len(term["factors"]) != 2:
            raise ValueError("Interaction terms must be products of exactly two features")
        for factor in term["factors"]:
            if factor not in features:
                raise ValueError(f"Interaction factor {factor} is not in the feature set")
    for regime in config["regimes"]:
        if regime["factor"] not in features:
            raise ValueError(f"Regime factor {regime['factor']} is not in the feature set")
    expected_family = len(features) + len(config["interactions"]) + 1
    if config["multiplicity"]["family_size"] != expected_family:
        raise ValueError(f"Multiplicity family_size must be {expected_family} (LOO + single terms + joint)")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


# ---------------------------------------------------------------------------
# Parent report / feature matrix
# ---------------------------------------------------------------------------


def _load_parent_report(config: dict) -> tuple[dict, dict, str]:
    parent_path = root() / "reports" / f"{config['parent_report']}.json"
    if not parent_path.exists():
        raise ValueError(f"Parent report {config['parent_report']} is missing; run the baseline first")
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    if list(parent["feature_names"]) != list(config["features"]):
        raise ValueError("state-vector features must match the parent report feature_names")
    if list(parent["horizons"]) != list(config["horizons"]):
        raise ValueError("state-vector horizons must match the parent report horizons")
    return parent, _load_parent_config(), parent_path.read_text(encoding="utf-8").strip()


def _load_feature_matrix(parent: dict, config: dict) -> list[dict]:
    path = root() / parent["files"]["features"]
    columns = ["feature_date"] + list(config["features"])
    for horizon in config["horizons"]:
        columns += [f"target_return_{horizon}d", f"target_end_{horizon}d"]
    quoted = ",".join(f'"{column}"' for column in columns)
    with duckdb.connect() as connection:
        rows = connection.execute(
            f"SELECT {quoted} FROM read_parquet(?) ORDER BY feature_date", [str(path)]
        ).fetchall()
    result = []
    for row in rows:
        item = {"feature_date": date.fromisoformat(str(row[0]))}
        for index, name in enumerate(config["features"]):
            value = row[index + 1]
            if value is None or not math.isfinite(float(value)):
                raise ValueError(f"Feature matrix contains a non-finite {name}")
            item[name] = float(value)
        for index, horizon in enumerate(config["horizons"]):
            item[f"target_return_{horizon}d"] = float(row[len(config["features"]) + 1 + index * 2])
            item[f"target_end_{horizon}d"] = date.fromisoformat(str(row[len(config["features"]) + 2 + index * 2]))
        result.append(item)
    if len(result) < 1000:
        raise ValueError("Feature matrix is too short")
    return result


# ---------------------------------------------------------------------------
# Pure scoring core (no I/O — unit-testable on synthetic rows)
# ---------------------------------------------------------------------------


def _binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided p-value for the sign test (H0: each fold's delta has
    a 50/50 chance of direction). p = 2 * P(X <= min(k, n-k)) so the most
    extreme result (k == n) yields 2/2^n, not 1.0."""
    if n == 0:
        return 1.0
    k = min(k, n - k)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def _bootstrap_mean_ci(deltas: list[float], reps: int, seed: int) -> tuple[float, float, float]:
    rng = random.Random(seed)
    n = len(deltas)
    means = []
    for _ in range(reps):
        means.append(fmean(deltas[rng.randrange(n)] for _ in range(n)))
    means.sort()
    lower = means[max(0, int(0.025 * reps) - 1) if reps > 20 else 0]
    upper = means[min(len(means) - 1, int(0.975 * reps))]
    return fmean(deltas), lower, upper


def _fold_splits(rows: list[dict], horizon: int, test_year: int, minimums: tuple[int, int, int]) -> dict | None:
    """Mirror of ``pipeline._walk_forward`` fold boundaries (purged by
    target_end_date, calendar-year validation)."""
    target = f"target_return_{horizon}d"
    target_end = f"target_end_{horizon}d"
    validation_start = date(test_year - 1, 1, 1)
    test_start, test_end = date(test_year, 1, 1), date(test_year, 12, 31)
    selection_train = [row for row in rows if row[target_end] < validation_start]
    validation = [row for row in rows if validation_start <= row["feature_date"] < test_start and row[target_end] < test_start]
    fit_rows = [row for row in rows if row[target_end] < test_start]
    test_rows = [row for row in rows if test_start <= row["feature_date"] <= test_end and row[target_end] <= test_end]
    if (len(selection_train) < minimums[0] or len(validation) < minimums[1] or len(test_rows) < minimums[2]):
        return None
    return {"target": target, "selection_train": selection_train, "validation": validation,
            "fit_rows": fit_rows, "test_rows": test_rows}


def _fit_variant(split: dict, columns: list[str], lambdas: list[float]) -> tuple[dict, float]:
    target = split["target"]
    best_lambda, best_score = None, math.inf
    for ridge in lambdas:
        candidate = _fit_ridge(split["selection_train"], columns, target, float(ridge))
        score = _mse([float(row[target]) for row in split["validation"]],
                     [_predict(candidate, row, columns) for row in split["validation"]])
        if score < best_score:
            best_lambda, best_score = float(ridge), score
    return _fit_ridge(split["fit_rows"], columns, target, best_lambda), best_lambda


def _standardized_vector(model: dict, row: dict, columns: list[str]) -> list[float]:
    return [1.0] + [(float(row[name]) - model["means"][index]) / model["scales"][index]
                    for index, name in enumerate(columns)]


def score_state_vector(rows: list[dict], feature_names: list[str], horizons: list[int],
                       interactions: list[dict], regimes: list[dict],
                       ridge_lambdas: list[float], minimums: tuple[int, int, int],
                       regime_minimums: tuple[int, int, int],
                       bootstrap: dict, permutation: dict) -> dict:
    """Score every pre-registered layer. Pure function: rows in, nested dict out."""
    term_columns = [f"term__{term['name']}" for term in interactions]
    joint_columns = list(feature_names) + term_columns
    for row in rows:
        for term, column in zip(interactions, term_columns):
            row[column] = float(row[term["factors"][0]]) * float(row[term["factors"][1]])

    variants: dict[str, list[str]] = {"baseline": list(feature_names)}
    for term, column in zip(interactions, term_columns):
        variants[f"term__{term['name']}"] = list(feature_names) + [column]
    variants["joint"] = list(joint_columns)
    for feature in feature_names:
        variants[f"loo__{feature}"] = [name for name in feature_names if name != feature]

    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    result: dict = {"horizons": {}}

    for horizon in horizons:
        target = f"target_return_{horizon}d"
        fold_records = []
        pooled: dict[str, list] = {name: [] for name in variants}
        pooled_actual: dict[str, list] = {name: [] for name in variants}
        pooled_baseline_mean: list[float] = []
        joint_pooled: list[tuple[list[float], float]] = []  # (design vector, actual) for permutation
        marginal_rows: list[dict] = []
        regime_acc: dict[str, dict[str, dict]] = {
            regime["name"]: {level: {"deltas": [], "mse": [], "mean_mse": [], "n_test": 0,
                                     "folds": 0, "skipped": 0,
                                     "coef": {name: [] for name in feature_names}}
                             for level in (regime["levels"][0]["above"], regime["levels"][0]["at_or_below"])}
            for regime in regimes
        }

        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
                for regime in regimes:
                    for level in (regime["levels"][0]["above"], regime["levels"][0]["at_or_below"]):
                        regime_acc[regime["name"]][level]["skipped"] += 1
                continue
            fold_id = f"{test_year}-{horizon}d"
            actual = [float(row[target]) for row in split["test_rows"]]
            historical_mean = fmean(float(row[target]) for row in split["fit_rows"])
            record = {"fold_id": fold_id, "horizon_sessions": horizon,
                      "train_rows": len(split["selection_train"]), "fit_rows": len(split["fit_rows"]),
                      "test_rows": len(split["test_rows"])}
            base_mse = None
            for name, columns in variants.items():
                model, best_lambda = _fit_variant(split, columns, ridge_lambdas)
                predicted = [_predict(model, row, columns) for row in split["test_rows"]]
                mse = _mse(actual, predicted)
                if name == "baseline":
                    base_mse = mse
                record[f"{name}_lambda"] = best_lambda
                record[f"{name}_mse"] = mse
                pooled[name].extend(predicted)
                pooled_actual[name].extend(actual)
                if name == "baseline":
                    pooled_baseline_mean.extend([historical_mean] * len(split["test_rows"]))
                if name == "joint":
                    joint_pooled.extend((_standardized_vector(model, row, joint_columns), float(row[target]),
                                        list(model["coefficients"]))
                                        for row in split["test_rows"])
                if name.startswith("loo__"):
                    pass
            marginal_rows.extend(split["test_rows"])
            # paired deltas versus the baseline fold
            for name in variants:
                if name == "baseline":
                    continue
                record[f"{name}_delta_mse"] = record[f"{name}_mse"] - base_mse
            # regimes (reduced pre-registered minimums, subset rows)
            for regime in regimes:
                factor, threshold = regime["factor"], float(regime["threshold"])
                for key in ("above", "at_or_below"):
                    level_name = regime["levels"][0]["above"] if key == "above" else regime["levels"][0]["at_or_below"]
                    subset = {
                        "target": target,
                        "selection_train": [r for r in split["selection_train"] if (float(r[factor]) > threshold) == (key == "above")],
                        "validation": [r for r in split["validation"] if (float(r[factor]) > threshold) == (key == "above")],
                        "fit_rows": [r for r in split["fit_rows"] if (float(r[factor]) > threshold) == (key == "above")],
                        "test_rows": [r for r in split["test_rows"] if (float(r[factor]) > threshold) == (key == "above")],
                    }
                    if (len(subset["selection_train"]) < regime_minimums[0]
                            or len(subset["validation"]) < regime_minimums[1]
                            or len(subset["test_rows"]) < regime_minimums[2]):
                        regime_acc[regime["name"]][level_name]["skipped"] += 1
                        continue
                    model, _ = _fit_variant(subset, list(feature_names), ridge_lambdas)
                    actual_sub = [float(row[target]) for row in subset["test_rows"]]
                    predicted_sub = [_predict(model, row, feature_names) for row in subset["test_rows"]]
                    acc = regime_acc[regime["name"]][level_name]
                    acc["folds"] += 1
                    acc["n_test"] += len(actual_sub)
                    acc["mse"].append(_mse(actual_sub, predicted_sub))
                    acc["mean_mse"].append(_mse(actual_sub, [fmean(float(row[target]) for row in subset["fit_rows"])] * len(actual_sub)))
                    for index, name in enumerate(feature_names):
                        acc["coef"][name].append(model["coefficients"][index + 1])
            fold_records.append(record)

        if not fold_records:
            raise ValueError(f"No valid walk-forward folds for horizon {horizon}")

        # ---- baseline aggregate (consistency anchor vs parent report) ----
        base_pred = pooled["baseline"]
        base_actual = pooled_actual["baseline"]
        base = {
            "out_of_sample_rows": len(base_pred), "folds": len(fold_records),
            "mse": _mse(base_actual, base_pred),
            "mean_baseline_mse": _mse(base_actual, pooled_baseline_mean),
            "skill_vs_mean": 1.0 - _mse(base_actual, base_pred) / _mse(base_actual, pooled_baseline_mean)
                              if _mse(base_actual, pooled_baseline_mean) else None,
            "mae": fmean(abs(a - p) for a, p in zip(base_actual, base_pred)),
            "correlation": _correlation(base_actual, base_pred),
            "sign_accuracy": fmean((a >= 0) == (p >= 0) for a, p in zip(base_actual, base_pred)),
        }

        # ---- marginal diagnostics (OOS univariate correlation only) ----
        marginal = {}
        for feature in feature_names:
            values = [float(row[feature]) for row in marginal_rows]
            targets = [float(row[target]) for row in marginal_rows]
            marginal[feature] = {"oos_correlation": _correlation(values, targets), "n": len(values),
                                 "note": "diagnostic only — not a feature-selection gate"}

        # ---- paired-delta inference for every variant ----
        alpha = 0.05
        family = len(feature_names) + len(interactions) + 1
        threshold = alpha / family

        def _variant_stats(name: str, direction: str) -> dict:
            deltas = [record[f"{name}_delta_mse"] for record in fold_records]
            n = len(deltas)
            if direction == "improve":
                k = sum(1 for d in deltas if d < 0)
            else:
                k = sum(1 for d in deltas if d > 0)
            p_value = _binom_two_sided(k, n)
            mean_delta, lower, upper = _bootstrap_mean_ci(deltas, int(bootstrap["reps"]), int(bootstrap["seed"]))
            return {
                "delta_mse_mean": mean_delta,
                "bootstrap_ci": [lower, upper],
                "folds": n,
                "folds_in_favor": k,
                "sign_test_p": p_value,
                "bonferroni_threshold": threshold,
                "significant": p_value < threshold,
            }

        interactions_out = {}
        for term in interactions:
            interactions_out[term["name"]] = _variant_stats(f"term__{term['name']}", "improve")
        joint_out = _variant_stats("joint", "improve")
        ablation_out = {feature: _variant_stats(f"loo__{feature}", "worsens") for feature in feature_names}

        # ---- permutation importance (pooled OOS, diagnostic only) ----
        # Linear-model exact form: prediction = sum_j c_j * z_j, so shuffling
        # column j's z-values changes each prediction by c_j * (z'_j - z_j).
        # Per-row coefficients are the fold model's (standardized units).
        importance = None
        if permutation and joint_pooled:
            rng = random.Random(int(permutation["seed"]))
            reps = int(permutation["reps"])
            count = len(joint_pooled)
            vectors = [entry[0] for entry in joint_pooled]
            actuals = [entry[1] for entry in joint_pooled]
            coefficient_sets = [entry[2] for entry in joint_pooled]
            base_prediction = [sum(c * v for c, v in zip(coeffs, vector))
                               for coeffs, vector in zip(coefficient_sets, vectors)]
            base_mse = _mse(actuals, base_prediction)
            importance = {}
            for position, column in enumerate(joint_columns):
                values_j = [vector[position] for vector in vectors]
                deltas = []
                for _ in range(reps):
                    permuted = values_j[:]
                    rng.shuffle(permuted)
                    perturbed = [base_prediction[i] + coefficient_sets[i][position] * (permuted[i] - values_j[i])
                                 for i in range(count)]
                    deltas.append(_mse(actuals, perturbed) - base_mse)
                importance[column] = {
                    "mean_delta_mse": fmean(deltas),
                    "mean_delta_mse_pct": 100.0 * fmean(deltas) / base_mse if base_mse else None,
                    "reps": reps,
                }

        # ---- regime summary ----
        regimes_out = {}
        for regime in regimes:
            regimes_out[regime["name"]] = {}
            for level_name in (regime["levels"][0]["above"], regime["levels"][0]["at_or_below"]):
                acc = regime_acc[regime["name"]][level_name]
                regimes_out[regime["name"]][level_name] = {
                    "folds": acc["folds"], "skipped_folds": acc["skipped"], "test_rows": acc["n_test"],
                    "mse": fmean(acc["mse"]) if acc["mse"] else None,
                    "mean_baseline_mse": fmean(acc["mean_mse"]) if acc["mean_mse"] else None,
                    "skill_vs_mean": (1.0 - fmean(acc["mse"]) / fmean(acc["mean_mse"])
                                      if acc["mse"] and acc["mean_mse"] else None),
                    "mean_coefficients_z_units": {name: fmean(acc["coef"][name]) if acc["coef"][name] else None
                                                  for name in feature_names},
                }

        result["horizons"][f"{horizon}d"] = {
            "baseline": base,
            "marginal": marginal,
            "interactions": interactions_out,
            "joint": joint_out,
            "ablation": ablation_out,
            "regimes": regimes_out,
            "permutation_importance": importance,
            "folds": fold_records,
        }
    return result


# ---------------------------------------------------------------------------
# Build (I/O)
# ---------------------------------------------------------------------------


def build_state_vector(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    parent, parent_config, _ = _load_parent_report(config)
    rows = _load_feature_matrix(parent, config)

    result = score_state_vector(
        rows, list(config["features"]), list(config["horizons"]), config["interactions"],
        config["regimes"], [float(value) for value in config["ridge_lambdas"]],
        (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]), int(config["minimum_test_rows"])),
        (int(config["regime_minimum_rows"]["train"]), int(config["regime_minimum_rows"]["validation"]),
         int(config["regime_minimum_rows"]["test"])),
        config["bootstrap"], config["permutation_importance"],
    )

    # Consistency anchor: the core's baseline walk-forward must reproduce the
    # parent (denn-baseline) aggregate exactly — same features, same fold logic.
    parent_aggregate = {item["horizon_sessions"]: item for item in parent["aggregate_metrics"]}
    for horizon in config["horizons"]:
        mine = result["horizons"][f"{horizon}d"]["baseline"]
        reference = parent_aggregate[horizon]
        for key in ("mse", "mean_baseline_mse", "skill_vs_mean", "mae", "correlation", "sign_accuracy"):
            if abs(mine[key] - reference[key]) > 1e-9:
                raise ValueError(f"Baseline consistency anchor failed for {horizon}d: {key} "
                                 f"{mine[key]} != {reference[key]}")

    dataset_id = hashlib.sha256(f"{config_hash}:{parent['dataset_id']}".encode()).hexdigest()[:20]
    folder = root() / "gold" / "denn_state_vector" / dataset_id
    fold_rows = []
    for horizon_label, horizon_data in result["horizons"].items():
        for record in horizon_data["folds"]:
            fold_rows.append(record)
    _write_parquet(fold_rows, folder / "fold_metrics.parquet", "horizon_sessions,fold_id")

    # Gold surface kept small by design: fold-level metrics only (per-row
    # predictions remain in the parent denn-baseline gold matrix).
    files = {"fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix()}

    factor_term_map = {feature: [] for feature in config["features"]}
    for term in config["interactions"]:
        for feature in term["factors"]:
            factor_term_map[feature].append(term["name"])

    report = {
        "dataset_id": dataset_id, "parser": config["version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "input_dataset_id": parent["dataset_id"], "config_sha256": config_hash,
        "parent_report": config["parent_report"], "parent_dataset_id": parent["dataset_id"],
        "feature_names": list(config["features"]), "horizons": list(config["horizons"]),
        "interactions": config["interactions"], "regimes": config["regimes"],
        "factor_terms": factor_term_map,
        "multiplicity": config["multiplicity"], "bootstrap": config["bootstrap"],
        "result": {label: {key: value for key, value in data.items() if key != "folds"}
                   for label, data in result["horizons"].items()},
        "files": files,
        "strict_pit_eligible": False,
        "inference_status": config["inference_status"],
        "limitations": [
            "All inputs are current-history downloads with unknown historical availability; results are non-strict.",
            "Marginal univariate correlations are diagnostics only and must not be used as a feature-selection gate.",
            "Interaction terms are theory-motivated and pre-registered; no combinatorial term search was performed.",
            "Ablation deltas measure non-redundant conditional contribution: a useful-but-redundant factor shows a small delta.",
            "Permutation importance is computed on the pooled OOS set and is diagnostic, outside the Bonferroni family.",
            "Regime splits are z-score > 0 by construction (data-free threshold); regime folds below the pre-registered minimums are skipped and counted.",
            "Metrics are descriptive out-of-sample diagnostics, not a trading strategy or causal result.",
        ],
    }
    atomic_json(root() / "reports" / "denn_state_vector.json", report)
    return report
