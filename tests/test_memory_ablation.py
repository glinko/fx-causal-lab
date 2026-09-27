"""Contracts and deterministic signal test for the v0.20 memory ablation."""
from datetime import date, timedelta
from pathlib import Path
import random

import pytest
import yaml

from fxlab.denn import memory_ablation as memory
from fxlab.denn.pipeline import age_decay

CONFIG = Path(__file__).resolve().parents[1] / "config" / "memory_ablation.yaml"


def _business_days(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def test_config_freezes_features_half_lives_and_family():
    config, digest = memory._load_config(CONFIG)
    derived = [item["name"] for item in config["decayed_features"]]
    assert config["version"] == "denn-memory-ablation-1"
    assert config["multiplicity"]["family_size"] == len(config["horizons"]) == 4
    assert len(config["baseline_features"]) == 16
    assert len(config["direct_memory_features"]) + len(derived) == 13
    assert all(item["half_life_days"] > 0 for item in config["decayed_features"])
    assert config["sample"]["no_imputation"] is True
    assert len(digest) == 64


def test_config_rejects_overlap_and_nonpositive_half_life(tmp_path):
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["direct_memory_features"][0] = config["baseline_features"][0]
    bad = tmp_path / "memory.yaml"
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="separate"):
        memory._load_config(bad)
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["decayed_features"][0]["half_life_days"] = 0
    bad.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        memory._load_config(bad)


def test_fixed_decay_is_deterministic_and_fresh_state_is_larger():
    value = 2.5
    assert value * age_decay(0, 14) == pytest.approx(value)
    assert value * age_decay(14, 14) == pytest.approx(value / 2)
    assert value * age_decay(28, 14) == pytest.approx(value / 4)


def test_registered_direction_detects_out_of_sample_memory_signal():
    rng = random.Random(20260926)
    rows = []
    for day in _business_days(date(2012, 1, 1), date(2026, 12, 31)):
        observed = rng.gauss(0, 1)
        temporal = rng.gauss(0, 1)
        target = 0.7 * temporal + rng.gauss(0, 0.2)
        rows.append({"feature_date": day, "observed": observed, "temporal": temporal,
                     "target_return_1d": target, "target_end_1d": day + timedelta(days=1)})
    result = memory.score_memory(rows, ["observed"], ["temporal"], [1], [0.1],
                                 (100, 20, 10), {"reps": 200, "seed": 42})["1d"]
    paired = result["paired_improvement"]
    assert paired["mean"] > 0
    assert paired["folds_in_favor"] == paired["folds"] == 9
    assert paired["sign_test_p"] < paired["bonferroni_threshold"]
    assert paired["significant"] is True
    assert result["treatment"]["mse"] < result["baseline"]["mse"]
