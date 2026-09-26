"""Unit tests for the v0.18 grouped (block) state-vector suite.

Pure Python, deterministic. Covers: the pre-registered block config
(partition contract), the block-ablation scoring on a synthetic matrix where
one block carries a redundant pair + one independent block carries the signal,
block permutation importance (a dead block must score ~0), within-block
collinearity diagnostics, and config-loader rejection rules.
"""
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from fxlab.denn import grouped as gp

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "grouped.yaml"

FEATURES = ["r1", "r2", "s1", "n1"]
BLOCKS = [
    {"name": "REDUNDANT", "members": ["r1", "r2"], "rationale": "t"},
    {"name": "SIGNAL", "members": ["s1"], "rationale": "t"},
    {"name": "NOISE", "members": ["n1"], "rationale": "t"},
]
LAMBDAS = [0.1]
MINIMUMS = (100, 20, 10)
BOOTSTRAP = {"reps": 200, "seed": 42}
PERMUTATION = {"reps": 50, "seed": 42}
GRID_START, GRID_END = date(2012, 1, 1), date(2026, 12, 31)
_CACHE: dict = {}


def _business_days(start: date, end: date) -> list[date]:
    days, cursor = [], start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def make_signal_rows() -> list[dict]:
    """s1 drives the target; r1/r2 are a collinear redundant pair (r2 ~ r1), n1 is noise."""
    import random
    rng = random.Random(2026)
    rows = []
    for day in _business_days(GRID_START, GRID_END):
        s1 = rng.gauss(0, 1)
        r1 = rng.gauss(0, 1)
        r2 = 0.97 * r1 + rng.gauss(0, 0.2)
        n1 = rng.gauss(0, 1)
        y = 0.5 * s1 + rng.gauss(0, 0.5)
        rows.append({"feature_date": day, "r1": r1, "r2": r2, "s1": s1, "n1": n1,
                     "target_return_1d": y, "target_end_1d": day + timedelta(days=1)})
    return rows


def make_noise_rows() -> list[dict]:
    import random
    rng = random.Random(99)
    rows = []
    for day in _business_days(GRID_START, GRID_END):
        rows.append({"feature_date": day, "r1": rng.gauss(0, 1), "r2": rng.gauss(0, 1),
                     "s1": rng.gauss(0, 1), "n1": rng.gauss(0, 1),
                     "target_return_1d": rng.gauss(0, 1),
                     "target_end_1d": day + timedelta(days=1)})
    return rows


def score(rows):
    return gp.score_grouped(rows, FEATURES, BLOCKS, [1], LAMBDAS, MINIMUMS, BOOTSTRAP, PERMUTATION)


def _signal_result() -> dict:
    if "signal" not in _CACHE:
        _CACHE["signal"] = score(make_signal_rows())
    return _CACHE["signal"]


def _noise_result() -> dict:
    if "noise" not in _CACHE:
        _CACHE["noise"] = score(make_noise_rows())
    return _CACHE["noise"]


# ---------------------------------------------------------------------------
# Pre-registered config
# ---------------------------------------------------------------------------


def test_config_is_well_formed_and_blocks_partition_features():
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    assert config["version"] == "denn-grouped-1"
    features = config["features"]
    assert len(features) == len(set(features))
    members = [member for block in config["blocks"] for member in block["members"]]
    assert sorted(members) == sorted(features)  # exact partition
    # family = one test per block
    assert config["multiplicity"]["family_size"] == len(config["blocks"])
    assert config["ablation"]["method"] == "leave_one_block_out"
    assert config["strict_pit_eligible"] is False
    loaded, _ = gp._load_config(CONFIG_PATH)
    assert loaded["blocks"] == config["blocks"]


def test_config_loader_rejects_incomplete_partition(tmp_path):
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    config["blocks"][0]["members"] = config["blocks"][0]["members"][:1]  # drop a member
    bad = tmp_path / "grouped.yaml"
    bad.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="partition"):
        gp._load_config(bad)


def test_config_loader_rejects_overlapping_blocks(tmp_path):
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    config["blocks"][1]["members"].append(config["blocks"][0]["members"][0])  # overlap
    bad = tmp_path / "grouped.yaml"
    bad.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="partition"):
        gp._load_config(bad)


def test_config_loader_rejects_unknown_version(tmp_path):
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    config["version"] = "denn-grouped-2"
    bad = tmp_path / "grouped.yaml"
    bad.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="version"):
        gp._load_config(bad)


# ---------------------------------------------------------------------------
# Block ablation on the synthetic signal case
# ---------------------------------------------------------------------------


def test_signal_block_ablation_is_negative_and_others_positive():
    horizon = _signal_result()["horizons"]["1d"]
    ablation = horizon["block_ablation"]
    baseline_mse = horizon["baseline"]["mse"]
    # removing the SIGNAL block must worsen the OOS fit (positive delta)
    assert ablation["SIGNAL"]["delta_mse_mean"] > 0.2 * baseline_mse
    assert ablation["SIGNAL"]["folds_in_favor"] > ablation["SIGNAL"]["folds"] // 2
    # removing the redundant/noise blocks must not help (delta >= 0 in mean,
    # at least not systematically negative)
    assert ablation["REDUNDANT"]["delta_mse_mean"] > -0.02 * baseline_mse
    assert ablation["NOISE"]["delta_mse_mean"] > -0.02 * baseline_mse


def test_block_sign_test_and_bonferroni_threshold():
    horizon = _signal_result()["horizons"]["1d"]
    ablation = horizon["block_ablation"]
    # family of 3 blocks -> threshold 0.05/3
    for block in BLOCKS:
        assert ablation[block["name"]]["bonferroni_threshold"] == pytest.approx(0.05 / len(BLOCKS))
        assert ablation[block["name"]]["folds"] == 9  # 2018..2026 folds
    # the signal block should pass the Bonferroni sign test on synthetic data
    assert ablation["SIGNAL"]["significant"] is True
    assert ablation["SIGNAL"]["sign_test_p"] < 0.05 / 3


def test_noise_case_no_block_is_significant():
    horizon = _noise_result()["horizons"]["1d"]
    for block in BLOCKS:
        assert horizon["block_ablation"][block["name"]]["significant"] is False


# ---------------------------------------------------------------------------
# Block permutation importance
# ---------------------------------------------------------------------------


def test_permutation_importance_dead_block_is_near_zero():
    horizon = _signal_result()["horizons"]["1d"]
    perm = horizon["block_permutation_importance"]
    # shuffling the noise block barely changes the pooled OOS MSE
    assert perm["NOISE"]["mean_delta_mse"] < 0.02 * horizon["baseline"]["mse"]
    # shuffling the signal block must hurt
    assert perm["SIGNAL"]["mean_delta_mse"] > 0.05 * horizon["baseline"]["mse"]
    # diagnostic note present, members listed
    assert perm["SIGNAL"]["members"] == ["s1"]
    assert "diagnostic" in perm["SIGNAL"]["note"]


def test_permutation_importance_deterministic():
    # determinism: same input rows -> identical importance numbers
    rows = make_signal_rows()
    first = gp.score_grouped(rows, FEATURES, BLOCKS, [1], LAMBDAS, MINIMUMS,
                             BOOTSTRAP, PERMUTATION)["horizons"]["1d"]["block_permutation_importance"]
    second = gp.score_grouped(rows, FEATURES, BLOCKS, [1], LAMBDAS, MINIMUMS,
                              BOOTSTRAP, PERMUTATION)["horizons"]["1d"]["block_permutation_importance"]
    assert first["SIGNAL"]["mean_delta_mse"] == second["SIGNAL"]["mean_delta_mse"]
    assert first["REDUNDANT"]["mean_delta_mse"] == second["REDUNDANT"]["mean_delta_mse"]


# ---------------------------------------------------------------------------
# Within-block collinearity diagnostics
# ---------------------------------------------------------------------------


def test_within_block_collinearity_high_for_redundant_pair():
    horizon = _signal_result()["horizons"]["1d"]
    pairs = horizon["block_correlations"]["REDUNDANT"]["pairs"]
    assert "r1~r2" in pairs
    assert abs(pairs["r1~r2"]["oos_correlation"]) > 0.9
    assert pairs["r1~r2"]["n"] == horizon["baseline"]["out_of_sample_rows"]


def test_single_member_block_has_empty_pairs():
    horizon = _signal_result()["horizons"]["1d"]
    assert horizon["block_correlations"]["SIGNAL"]["pairs"] == {}
