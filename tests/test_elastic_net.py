"""Tests for the v0.22 sparse supervised model."""
from datetime import date, timedelta
from pathlib import Path
import random

import pytest
import yaml

from fxlab.denn import elastic_net

CONFIG = Path(__file__).resolve().parents[1] / "config" / "elastic_net.yaml"


def test_config_freezes_feature_and_parameter_grids():
    config, digest = elastic_net._load_config(CONFIG)
    assert config["version"] == "denn-elastic-net-1"
    assert len(config["features"]) == 16
    assert config["multiplicity"]["family_size"] == len(config["horizons"]) == 4
    assert config["elastic_net"]["selection"] == "cyclic"
    assert config["elastic_net"]["tie_break"] == "stronger_regularization"
    assert config["sample"]["no_imputation"] is True
    assert len(digest) == 64


def test_config_rejects_invalid_l1_ratio(tmp_path):
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["elastic_net"]["l1_ratios"] = [0]
    bad = tmp_path / "elastic.yaml"
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        elastic_net._load_config(bad)


def test_sparse_fit_keeps_the_real_driver_and_drops_noise():
    rng = random.Random(31)
    rows = []
    for _ in range(1500):
        values = [rng.gauss(0, 1) for _ in range(6)]
        row = {f"x{index}": value for index, value in enumerate(values)}
        row["target"] = 0.8 * values[0] + rng.gauss(0, 0.1)
        rows.append(row)
    features = [f"x{index}" for index in range(6)]
    model = elastic_net.fit_elastic_net(rows, features, "target", 0.03, 1.0, 20000, 1e-8)
    assert "x0" in model["nonzero_features"]
    assert len(model["nonzero_features"]) <= 2
    predictions = elastic_net.predict_elastic_net(model, rows[:10], features)
    assert len(predictions) == 10


def test_scoring_reports_year_by_year_comparison():
    rng = random.Random(32)
    rows = []
    cursor = date(2014, 1, 1)
    while cursor <= date(2026, 12, 31):
        if cursor.weekday() < 5:
            signal, noise = rng.gauss(0, 1), rng.gauss(0, 1)
            rows.append({"feature_date": cursor, "signal": signal, "noise": noise,
                         "target_return_1d": 0.25 * signal + rng.gauss(0, 0.4),
                         "target_end_1d": cursor + timedelta(days=1)})
        cursor += timedelta(days=1)
    settings = {"alphas": [0.001, 0.01], "l1_ratios": [0.5, 1.0],
                "max_iter": 20000, "tolerance": 1e-8}
    result = elastic_net.score_elastic_net(rows, ["signal", "noise"], [1], [0.1], settings,
                                           (100, 20, 10), {"reps": 100, "seed": 42})["1d"]
    assert result["paired_improvement"]["folds"] == 7
    assert 0 <= result["selection"]["feature_frequency"]["signal"] <= 1
    assert result["treatment"]["mse"] > 0
