"""Tests for the v0.23 nonlinear comparison."""
from datetime import date, timedelta
from pathlib import Path
import random

import pytest
import yaml

from fxlab.denn import nonlinear_boosting

CONFIG = Path(__file__).resolve().parents[1] / "config" / "nonlinear_boosting.yaml"


def test_config_freezes_same_features_and_four_horizons():
    config, digest = nonlinear_boosting._load_config(CONFIG)
    assert config["version"] == "denn-nonlinear-boosting-1"
    assert len(config["features"]) == 16
    assert config["multiplicity"]["family_size"] == len(config["horizons"]) == 4
    assert config["gradient_boosting"]["early_stopping"] is False
    assert config["gradient_boosting"]["tie_break"] == "simpler_model"
    assert config["sample"]["no_imputation"] is True
    assert len(digest) == 64


def test_config_rejects_automatic_early_stopping(tmp_path):
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["gradient_boosting"]["early_stopping"] = True
    bad = tmp_path / "nonlinear.yaml"
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="deterministic"):
        nonlinear_boosting._load_config(bad)


def test_boosting_learns_an_interaction_that_a_flat_average_misses():
    rng = random.Random(44)
    rows = []
    for _ in range(1800):
        x1, x2 = rng.uniform(-1, 1), rng.uniform(-1, 1)
        rows.append({"x1": x1, "x2": x2, "target": x1 * x2 + rng.gauss(0, 0.03)})
    settings = {"loss": "squared_error", "learning_rate": 0.06, "max_leaf_nodes": 15,
                "l2_regularization": 0.1, "max_iter": 200, "min_samples_leaf": 20,
                "max_bins": 63, "early_stopping": False, "random_state": 42}
    model = nonlinear_boosting.fit_boosting(rows[:1400], ["x1", "x2"], "target", settings)
    actual = [row["target"] for row in rows[1400:]]
    predicted = nonlinear_boosting.predict_boosting(model, rows[1400:], ["x1", "x2"])
    model_mse = sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual)
    mean_mse = sum((a - sum(actual) / len(actual)) ** 2 for a in actual) / len(actual)
    assert model_mse < mean_mse * 0.2


def test_scoring_reports_year_by_year_comparison():
    rng = random.Random(45)
    rows = []
    cursor = date(2014, 1, 1)
    while cursor <= date(2026, 12, 31):
        if cursor.weekday() < 5:
            x1, x2 = rng.uniform(-1, 1), rng.uniform(-1, 1)
            rows.append({"feature_date": cursor, "x1": x1, "x2": x2,
                         "target_return_1d": x1 * x2 + rng.gauss(0, 0.15),
                         "target_end_1d": cursor + timedelta(days=1)})
        cursor += timedelta(days=1)
    elastic = {"alphas": [0.001], "l1_ratios": [1.0], "max_iter": 20000, "tolerance": 1e-8}
    boost = {"loss": "squared_error", "learning_rates": [0.06], "max_leaf_nodes": [15],
             "l2_regularizations": [0.1], "max_iter": 120, "min_samples_leaf": 20,
             "max_bins": 63, "early_stopping": False, "random_state": 42}
    result = nonlinear_boosting.score_nonlinear(
        rows, ["x1", "x2"], [1], elastic, boost, (100, 20, 10), {"reps": 100, "seed": 42})["1d"]
    assert result["paired_improvement"]["folds"] == 7
    assert result["paired_improvement"]["folds_in_favor"] >= 6
    assert result["treatment"]["mse"] < result["baseline"]["mse"]
