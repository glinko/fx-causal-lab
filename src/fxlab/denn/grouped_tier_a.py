"""Pre-registered grouped experiment on the expanded Tier A world-state."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import yaml

from ..store import atomic_json, root
from .grouped import score_grouped
from .pipeline import _write_parquet

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "grouped_tier_a.yaml"


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-grouped-tier-a-1":
        raise ValueError("Unsupported Tier A grouped config version")
    required = {"parent_report", "features", "horizons", "ridge_lambdas", "blocks",
                "sample", "ablation", "permutation", "multiplicity", "bootstrap"}
    if not required <= set(config):
        raise ValueError(f"Tier A grouped config is missing keys: {sorted(required - set(config))}")
    if sorted(config["horizons"]) != [1, 5, 20, 60]:
        raise ValueError("Tier A grouped horizons must remain 1/5/20/60")
    members = [member for block in config["blocks"] for member in block["members"]]
    if (len({block["name"] for block in config["blocks"]}) != len(config["blocks"])
            or sorted(members) != sorted(config["features"])):
        raise ValueError("Tier A blocks must uniquely partition the feature set")
    if config["multiplicity"]["family_size"] != len(config["blocks"]):
        raise ValueError("Multiplicity family_size must equal the number of blocks")
    if config["sample"].get("method") != "common_complete_case" or not config["sample"].get("no_imputation"):
        raise ValueError("Tier A grouped suite requires common complete cases without imputation")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _load_rows(config: dict) -> tuple[list[dict], dict]:
    report_path = root() / "reports" / f"{config['parent_report']}.json"
    if not report_path.exists():
        raise ValueError("tier_a_features.json is missing; run tier-a-features first")
    parent = json.loads(report_path.read_text(encoding="utf-8"))
    feature_path = root() / parent["files"]["features"]
    columns = ["feature_date"] + list(config["features"])
    for horizon in config["horizons"]:
        columns += [f"target_return_{horizon}d", f"target_end_{horizon}d"]
    quoted = ",".join(f'"{column}"' for column in columns)
    with duckdb.connect() as connection:
        cursor = connection.execute(f"SELECT {quoted} FROM read_parquet(?) ORDER BY feature_date", [str(feature_path)])
        raw = cursor.fetchall()
    rows = []
    for values in raw:
        if any(value is None for value in values[1:]):
            continue
        item = {"feature_date": date.fromisoformat(str(values[0]))}
        offset = 1
        for feature in config["features"]:
            value = float(values[offset]); offset += 1
            if not math.isfinite(value):
                raise ValueError(f"Non-finite Tier A feature: {feature}")
            item[feature] = value
        for horizon in config["horizons"]:
            target = float(values[offset]); target_end = values[offset + 1]; offset += 2
            if not math.isfinite(target):
                raise ValueError(f"Non-finite target_return_{horizon}d")
            item[f"target_return_{horizon}d"] = target
            item[f"target_end_{horizon}d"] = date.fromisoformat(str(target_end))
        rows.append(item)
    if len(rows) < 2500:
        raise ValueError("Tier A complete-case matrix is too short for the registered suite")
    expected = date.fromisoformat(str(config["sample"]["expected_start_not_before"]))
    if rows[0]["feature_date"] < expected:
        raise ValueError("Tier A complete-case interval begins before the registered source boundary")
    return rows, parent


def build_grouped_tier_a(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    rows, parent = _load_rows(config)
    result = score_grouped(
        rows, list(config["features"]), config["blocks"], list(config["horizons"]),
        [float(value) for value in config["ridge_lambdas"]],
        (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]),
         int(config["minimum_test_rows"])), config["bootstrap"], config["permutation"],
    )
    dataset_id = hashlib.sha256(f"{config_hash}:{parent['dataset_id']}".encode()).hexdigest()[:20]
    folder = root() / "gold" / "denn_grouped_tier_a" / dataset_id
    fold_rows = [record for data in result["horizons"].values() for record in data["folds"]]
    _write_parquet(fold_rows, folder / "fold_metrics.parquet", "horizon_sessions,fold_id")
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "generated_at": datetime.now(UTC).isoformat(),
        "input_dataset_id": parent["dataset_id"], "config_sha256": config_hash,
        "parent_report": config["parent_report"], "feature_names": list(config["features"]),
        "blocks": config["blocks"], "horizons": list(config["horizons"]),
        "sample": json.loads(json.dumps(config["sample"], default=str)),
        "sample_rows": len(rows), "date_from": str(rows[0]["feature_date"]), "date_to": str(rows[-1]["feature_date"]),
        "ablation": config["ablation"], "permutation": config["permutation"],
        "multiplicity": config["multiplicity"], "bootstrap": config["bootstrap"],
        "result": {label: {key: value for key, value in data.items() if key != "folds"}
                   for label, data in result["horizons"].items()},
        "files": {"fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix()},
        "strict_pit_eligible": False, "inference_status": config["inference_status"],
        "limitations": [
            "All inputs are current-history downloads without proven historical vintages; results are non-strict.",
            "The suite uses one common complete-case interval and never imputes missing observations.",
            "TIC is excluded because 2021+ history cannot support the registered walk-forward burn-in.",
            "Age/decay columns are reserved for a separate memory ablation.",
            "Permutation importance and within-block correlations are diagnostic-only.",
            "Results are predictive diagnostics, not causal effects or a trading strategy.",
        ],
    }
    atomic_json(root() / "reports" / "denn_grouped_tier_a.json", report)
    return report
