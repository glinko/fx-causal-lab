"""Contracts and leakage checks for the v0.21 fold-local block PCA."""
from datetime import date, timedelta
from pathlib import Path
import random

import pytest
import yaml

from fxlab.denn import block_pca

CONFIG = Path(__file__).resolve().parents[1] / "config" / "block_pca.yaml"


def test_config_is_a_complete_partition_and_four_horizon_family():
    config, digest = block_pca._load_config(CONFIG)
    members = [member for block in config["blocks"] for member in block["members"]]
    assert config["version"] == "denn-block-pca-1"
    assert sorted(members) == sorted(config["features"])
    assert len(config["blocks"]) == 7
    assert config["multiplicity"]["family_size"] == len(config["horizons"]) == 4
    assert config["compression"]["fit_scope"] == "past_rows_only"
    assert len(digest) == 64


def test_config_rejects_overlap(tmp_path):
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["blocks"][1]["members"].append(config["blocks"][0]["members"][0])
    bad = tmp_path / "pca.yaml"
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="partition"):
        block_pca._load_config(bad)


def test_correlated_pair_compresses_to_one_deterministic_component():
    rng = random.Random(21)
    rows = []
    for _ in range(1000):
        latent = rng.gauss(0, 1)
        rows.append({"a": latent + rng.gauss(0, 0.05), "b": latent + rng.gauss(0, 0.05)})
    blocks = [{"name": "signal", "members": ["a", "b"]}]
    model = block_pca.fit_block_pca(rows, blocks)
    assert model["signal"]["explained_variance_share"] > 0.99
    assert all(value > 0 for value in model["signal"]["loadings"])
    first = block_pca.transform_block_states(rows[:2], model)
    second = block_pca.transform_block_states(rows[:2], model)
    assert first == second


def test_test_rows_cannot_change_past_fitted_loadings():
    rng = random.Random(22)
    train = [{"a": rng.gauss(0, 1), "b": rng.gauss(0, 1)} for _ in range(300)]
    blocks = [{"name": "state", "members": ["a", "b"]}]
    before = block_pca.fit_block_pca(train, blocks)
    future = [{"a": 1e9, "b": -1e9} for _ in range(100)]
    block_pca.transform_block_states(future, before)
    after = block_pca.fit_block_pca(train, blocks)
    assert before == after


def test_scoring_emits_paired_fold_metrics():
    rng = random.Random(23)
    rows = []
    cursor = date(2014, 1, 1)
    while cursor <= date(2026, 12, 31):
        if cursor.weekday() < 5:
            latent = rng.gauss(0, 1)
            a, b = latent + rng.gauss(0, 0.2), latent + rng.gauss(0, 0.2)
            rows.append({"feature_date": cursor, "a": a, "b": b,
                         "target_return_1d": 0.2 * latent + rng.gauss(0, 0.5),
                         "target_end_1d": cursor + timedelta(days=1)})
        cursor += timedelta(days=1)
    result = block_pca.score_block_pca(rows, ["a", "b"], [{"name": "x", "members": ["a", "b"]}],
                                       [1], [0.1], (100, 20, 10), {"reps": 100, "seed": 42})["1d"]
    assert result["paired_improvement"]["folds"] == 7
    assert 0 <= result["mean_explained_variance"]["x"] <= 1
    assert result["baseline"]["mse"] > 0
    assert result["treatment"]["mse"] > 0
