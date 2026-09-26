"""v0.18 Group-level (block) state-vector suite.

The unit of analysis is the economic state BLOCK, not a single column. This
suite measures
block-level conditional contribution on the FROZEN v0.14 matrix:

1. leave-one-block-out ablation — the Bonferroni family (one test per block),
   same purged walk-forward, sign test and bootstrap CI as v0.17;
2. block permutation importance — diagnostic, outside the family: all of a
   block's standardized columns are shuffled together in the fitted full model;
3. within-block pairwise correlations on pooled OOS rows — diagnostic.

No new data, no transforms, no per-column lag selection. The protocol is
pre-registered in ``config/grouped.yaml`` (version ``denn-grouped-1``).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean

import yaml

from ..store import atomic_json, root
from .pipeline import _correlation, _fit_ridge, _mse, _predict, _write_parquet
from .state_vector import _binom_two_sided, _bootstrap_mean_ci, _fit_variant, _fold_splits, _load_feature_matrix, _load_parent_report, _standardized_vector

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "grouped.yaml"


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-grouped-1":
        raise ValueError("Unsupported grouped config version")
    required = {"parent_report", "features", "horizons", "ridge_lambdas", "blocks",
                "ablation", "permutation", "multiplicity", "bootstrap"}
    if not required <= set(config):
        raise ValueError(f"grouped config is missing keys: {sorted(required - set(config))}")
    if sorted(config["horizons"]) != [1, 5, 20, 60]:
        raise ValueError("grouped horizons must remain 1/5/20/60")
    if config["ablation"].get("method") != "leave_one_block_out":
        raise ValueError("grouped ablation method must be leave_one_block_out")
    # Blocks must partition the feature set exactly: every feature in exactly
    # one block, no unknown members, non-empty blocks. Frozen contract.
    members: list[str] = []
    names = set()
    for block in config["blocks"]:
        if not block["members"] or block["name"] in names:
            raise ValueError("Group blocks must be non-empty and uniquely named")
        names.add(block["name"])
        members.extend(block["members"])
    if sorted(members) != sorted(config["features"]):
        raise ValueError("Group blocks must partition the feature set exactly (union == features, no overlap)")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


# ---------------------------------------------------------------------------
# Pure scoring core (no I/O — unit-testable on synthetic rows)
# ---------------------------------------------------------------------------


def score_grouped(rows: list[dict], feature_names: list[str], blocks: list[dict], horizons: list[int],
                  ridge_lambdas: list[float], minimums: tuple[int, int, int],
                  bootstrap: dict, permutation: dict) -> dict:
    """Score every pre-registered block layer. Pure function: rows in, nested dict out."""
    variants: dict[str, list[str]] = {"baseline": list(feature_names)}
    for block in blocks:
        excluded = set(block["members"])
        variants[f"block__{block['name']}"] = [name for name in feature_names if name not in excluded]

    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    result: dict = {"horizons": {}}

    for horizon in horizons:
        target = f"target_return_{horizon}d"
        fold_records = []
        pooled_actual: list[float] = []
        pooled_baseline_mean: list[float] = []
        pooled_prediction: list[float] = []
        pooled_rows: list[dict] = []
        full_pooled: list[tuple[str, list[float], float, list[float]]] = []
        # (fold id, design vector including intercept, actual, coefficients)

        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
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
                    pooled_prediction.extend(predicted)
                    pooled_actual.extend(actual)
                    pooled_baseline_mean.extend([historical_mean] * len(split["test_rows"]))
                    pooled_rows.extend(split["test_rows"])
                    for row in split["test_rows"]:
                        full_pooled.append((fold_id, _standardized_vector(model, row, columns),
                                            float(row[target]), list(model["coefficients"])))
                record[f"{name}_lambda"] = best_lambda
                record[f"{name}_mse"] = mse
            for name in variants:
                if name != "baseline":
                    record[f"{name}_delta_mse"] = record[f"{name}_mse"] - base_mse
            fold_records.append(record)

        if not fold_records:
            raise ValueError(f"No valid walk-forward folds for horizon {horizon}")

        # ---- baseline aggregate (consistency anchor vs parent report) ----
        base = {
            "out_of_sample_rows": len(pooled_prediction), "folds": len(fold_records),
            "mse": _mse(pooled_actual, pooled_prediction),
            "mean_baseline_mse": _mse(pooled_actual, pooled_baseline_mean),
            "skill_vs_mean": 1.0 - _mse(pooled_actual, pooled_prediction) / _mse(pooled_actual, pooled_baseline_mean)
                              if _mse(pooled_actual, pooled_baseline_mean) else None,
            "mae": fmean(abs(a - p) for a, p in zip(pooled_actual, pooled_prediction)),
            "correlation": _correlation(pooled_actual, pooled_prediction),
            "sign_accuracy": fmean((a >= 0) == (p >= 0) for a, p in zip(pooled_actual, pooled_prediction)),
        }

        # ---- leave-one-block-out inference (the Bonferroni family) ----
        alpha = 0.05
        threshold = alpha / len(blocks)

        def _block_stats(name: str) -> dict:
            deltas = [record[f"{name}_delta_mse"] for record in fold_records]
            n = len(deltas)
            k = sum(1 for d in deltas if d > 0)  # removing the block worsens OOS fit
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

        ablation_out = {block["name"]: _block_stats(f"block__{block['name']}") for block in blocks}

        # ---- block permutation importance (diagnostic, outside the family) ----
        # Pre-registered construction ("all of a block's columns shuffled
        # together"): in each rep ONE random row permutation pi is applied to
        # every member column, so the block's JOINT state is preserved (its
        # internal correlations are intact) while the block->target link is
        # broken. This measures how much the model leans on the block AS A
        # UNIT. For a single-member block this reduces exactly to the v0.17
        # column permutation. Exact linear form: prediction = sum_j c_j * z_j,
        # so the change at row i is sum over member positions p of
        # c_p * (z_p[pi(i)] - z_p[i]). Coefficients are the row's fold model
        # (z units).
        importance = None
        if permutation and full_pooled:
            rng = random.Random(int(permutation["seed"]))
            reps = int(permutation["reps"])
            count = len(full_pooled)
            fold_ids = [entry[0] for entry in full_pooled]
            vectors = [entry[1] for entry in full_pooled]
            actuals = [entry[2] for entry in full_pooled]
            coefficient_sets = [entry[3] for entry in full_pooled]
            fold_members: dict[str, list[int]] = {}
            for index, fold_id in enumerate(fold_ids):
                fold_members.setdefault(fold_id, []).append(index)
            base_prediction = [sum(c * v for c, v in zip(coeffs, vector))
                               for coeffs, vector in zip(coefficient_sets, vectors)]
            base_mse = _mse(actuals, base_prediction)
            importance = {}
            for block in blocks:
                positions = [1 + feature_names.index(name) for name in block["members"]]
                columns = {position: [vector[position] for vector in vectors] for position in positions}
                deltas = []
                for _ in range(reps):
                    # Apply one common permutation to all columns in a block,
                    # but keep it inside each walk-forward test fold. Cross-fold
                    # standardized values are not comparable because every
                    # model has its own training mean and scale.
                    order = list(range(count))
                    for members in fold_members.values():
                        sources = members[:]
                        rng.shuffle(sources)
                        for destination, source in zip(members, sources):
                            order[destination] = source
                    perturbed = []
                    for i in range(count):
                        source = order[i]
                        shift = sum(coefficient_sets[i][position]
                                    * (columns[position][source] - vectors[i][position])
                                    for position in positions)
                        perturbed.append(base_prediction[i] + shift)
                    deltas.append(_mse(actuals, perturbed) - base_mse)
                importance[block["name"]] = {
                    "members": list(block["members"]),
                    "mean_delta_mse": fmean(deltas),
                    "mean_delta_mse_pct": 100.0 * fmean(deltas) / base_mse if base_mse else None,
                    "reps": reps,
                    "shuffle_scope": "within_walk_forward_fold",
                    "note": ("diagnostic only — outside the Bonferroni family; one common row permutation is "
                             "applied within each test fold to all member columns, preserving the block's joint state "
                             "(reduces to the v0.17 column permutation for a single-member block)"),
                }

        # ---- within-block pairwise correlations (diagnostic) ----
        block_correlations = {}
        for block in blocks:
            if len(block["members"]) < 2:
                block_correlations[block["name"]] = {"n_members": len(block["members"]), "pairs": {}}
                continue
            pairs = {}
            for a in range(len(block["members"])):
                for b in range(a + 1, len(block["members"])):
                    fa, fb = block["members"][a], block["members"][b]
                    pairs[f"{fa}~{fb}"] = {
                        "oos_correlation": _correlation([float(row[fa]) for row in pooled_rows],
                                                        [float(row[fb]) for row in pooled_rows]),
                        "n": len(pooled_rows),
                        "note": "diagnostic only — quantifies within-block collinearity",
                    }
            block_correlations[block["name"]] = {"n_members": len(block["members"]), "pairs": pairs}

        result["horizons"][f"{horizon}d"] = {
            "baseline": base,
            "block_ablation": ablation_out,
            "block_permutation_importance": importance,
            "block_correlations": block_correlations,
            "folds": fold_records,
        }
    return result


# ---------------------------------------------------------------------------
# Build (I/O)
# ---------------------------------------------------------------------------


def build_grouped(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    parent, _parent_config, _ = _load_parent_report(config)
    rows = _load_feature_matrix(parent, config)

    result = score_grouped(
        rows, list(config["features"]), config["blocks"], list(config["horizons"]),
        [float(value) for value in config["ridge_lambdas"]],
        (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]), int(config["minimum_test_rows"])),
        config["bootstrap"], config["permutation"],
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

    # Cross-reference (diagnostic): block delta vs the SUM of its members'
    # v0.17 single-feature LOO deltas. Descriptive only, no p-value.
    cross_reference = None
    ref_path = root() / "reports" / f"{config['cross_reference_report']}.json"
    if ref_path.exists():
        reference = json.loads(ref_path.read_text(encoding="utf-8"))
        cross_reference = {}
        for horizon in config["horizons"]:
            label = f"{horizon}d"
            block_deltas = result["horizons"][label]["block_ablation"]
            single_loo = reference["result"][label]["ablation"]
            cross_reference[label] = {}
            for block in config["blocks"]:
                member_sum = sum(single_loo[name]["delta_mse_mean"] for name in block["members"])
                cross_reference[label][block["name"]] = {
                    "block_delta_mse_mean": block_deltas[block["name"]]["delta_mse_mean"],
                    "sum_member_single_loo_deltas": member_sum,
                    "gap": block_deltas[block["name"]]["delta_mse_mean"] - member_sum,
                    "note": "gap = within-block redundancy/synergy diagnostic, descriptive only",
                }

    dataset_id = hashlib.sha256(f"{config_hash}:{parent['dataset_id']}".encode()).hexdigest()[:20]
    folder = root() / "gold" / "denn_grouped" / dataset_id
    fold_rows = []
    for horizon_label, horizon_data in result["horizons"].items():
        for record in horizon_data["folds"]:
            fold_rows.append(record)
    _write_parquet(fold_rows, folder / "fold_metrics.parquet", "horizon_sessions,fold_id")
    files = {"fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix()}

    report = {
        "dataset_id": dataset_id, "parser": config["version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "input_dataset_id": parent["dataset_id"], "config_sha256": config_hash,
        "parent_report": config["parent_report"], "parent_dataset_id": parent["dataset_id"],
        "feature_names": list(config["features"]), "blocks": config["blocks"], "horizons": list(config["horizons"]),
        "ablation": config["ablation"], "permutation": config["permutation"],
        "multiplicity": config["multiplicity"], "bootstrap": config["bootstrap"],
        "result": {label: {key: value for key, value in data.items() if key != "folds"}
                   for label, data in result["horizons"].items()},
        "cross_reference": cross_reference,
        "files": files,
        "strict_pit_eligible": False,
        "inference_status": config["inference_status"],
        "limitations": [
            "All inputs are current-history downloads with unknown historical availability; results are non-strict.",
            "Blocks are frozen on the existing 6-column matrix; the wider v2 world-state architecture (inflation expectations, flows, funding, relative equities, ...) is a data-acquisition plan and is NOT part of this run.",
            "Block ablation deltas measure non-redundant conditional contribution of a block beyond the other blocks; a single-feature ablation asks a different question and is not interchangeable with it.",
            "Block permutation importance and within-block correlations are pooled-OOS diagnostics and are outside the Bonferroni family.",
            "The cross-reference gap vs v0.17 single-feature LOO sums is descriptive (redundancy/synergy diagnostic), not a test.",
            "Metrics are descriptive out-of-sample diagnostics, not a trading strategy or causal result.",
        ],
    }
    atomic_json(root() / "reports" / "denn_grouped.json", report)
    return report
