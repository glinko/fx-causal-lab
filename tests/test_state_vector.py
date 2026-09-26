"""Unit tests for the v0.17 state-vector suite (pure Python, deterministic).

Covers: the pre-registered protocol config, the XOR emergent-signal case
(weak marginals, strong joint term), fold-boundary mirroring of the parent
walk-forward, and the inference primitives.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from fxlab.denn import state_vector as sv

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "state_vector.yaml"

# Synthetic protocol: 3 features, 1 interaction, 1 regime.
# Bonferroni family here = 3 LOO + 1 term + 1 joint = 5 (threshold 0.01).
FEATURES = ["a", "b", "c"]
INTERACTIONS = [{"name": "a_x_b", "factors": ["a", "b"], "channel": "xor"}]
REGIMES = [{"name": "a_regime", "factor": "a", "threshold": 0.0,
            "levels": [{"above": "a_hi", "at_or_below": "a_lo"}]}]
MINIMUMS = (100, 20, 10)
REGIME_MINIMUMS = (50, 10, 5)
LAMBDAS = [0.1]
BOOTSTRAP = {"reps": 100, "seed": 42}
PERMUTATION = {"reps": 20, "seed": 42}
# 9 walk-forward folds (2018..2026): with a perfectly consistent sign pattern
# the exact two-sided binomial p is 2/2^9 = 0.0039 < 0.01 threshold.
GRID_START, GRID_END = date(2012, 1, 1), date(2026, 12, 31)

_CACHE: dict = {}


def _business_days(start: date, end: date) -> list[date]:
    days, cursor = [], start
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def make_xor_rows() -> list[dict]:
    """Pure XOR target: y = a*b + tiny noise, a,b in {+1,-1}, c = noise.

    Marginal correlations of a and b with y are ~0 by construction, but the
    product term a*b determines y almost exactly.
    """
    import random
    rng = random.Random(1234)
    rows = []
    for day in _business_days(GRID_START, GRID_END):
        a = 1.0 if rng.random() < 0.5 else -1.0
        b = 1.0 if rng.random() < 0.5 else -1.0
        c = rng.gauss(0, 1)
        y = a * b + rng.gauss(0, 0.02)
        rows.append({"feature_date": day, "a": a, "b": b, "c": c,
                     "target_return_1d": y, "target_end_1d": day + timedelta(days=1)})
    return rows


def make_noise_rows() -> list[dict]:
    """Null case: y independent of all features."""
    import random
    rng = random.Random(99)
    rows = []
    for day in _business_days(GRID_START, GRID_END):
        rows.append({"feature_date": day,
                     "a": 1.0 if rng.random() < 0.5 else -1.0,
                     "b": 1.0 if rng.random() < 0.5 else -1.0,
                     "c": rng.gauss(0, 1),
                     "target_return_1d": rng.gauss(0, 1),
                     "target_end_1d": day + timedelta(days=1)})
    return rows


def score(rows):
    return sv.score_state_vector(rows, FEATURES, [1], INTERACTIONS, REGIMES,
                                 LAMBDAS, MINIMUMS, REGIME_MINIMUMS, BOOTSTRAP, PERMUTATION)


def _xor_result() -> dict:
    if "xor" not in _CACHE:
        _CACHE["xor"] = score(make_xor_rows())
    return _CACHE["xor"]


def _noise_result() -> dict:
    if "noise" not in _CACHE:
        _CACHE["noise"] = score(make_noise_rows())
    return _CACHE["noise"]


# ---------------------------------------------------------------------------
# Pre-registered config
# ---------------------------------------------------------------------------


def test_config_is_well_formed_and_family_matches_layers():
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    assert config["version"] == "denn-state-vector-1"
    features = config["features"]
    assert len(features) == len(set(features))
    # family = 6 LOO ablations + 5 single interaction terms + 1 joint model
    assert config["multiplicity"]["family_size"] == len(features) + len(config["interactions"]) + 1
    for term in config["interactions"]:
        assert len(term["factors"]) == 2
        assert set(term["factors"]) <= set(features)
    for regime in config["regimes"]:
        assert regime["factor"] in features
    assert config["strict_pit_eligible"] is False
    # loader validates the same constraints
    loaded, _ = sv._load_config(CONFIG_PATH)
    assert loaded["features"] == features


def test_config_loader_rejects_wrong_family_size(tmp_path):
    config = yaml.safe_load(CONFIG_PATH.read_bytes())
    config["multiplicity"]["family_size"] = 99
    bad = tmp_path / "state_vector.yaml"
    bad.write_text(yaml.safe_dump(config))
    with pytest.raises(ValueError, match="family_size"):
        sv._load_config(bad)


# ---------------------------------------------------------------------------
# Inference primitives
# ---------------------------------------------------------------------------


def test_binom_two_sided_exact_values():
    assert sv._binom_two_sided(0, 10) == pytest.approx(2.0 / 1024.0)
    assert sv._binom_two_sided(5, 10) == pytest.approx(1.0)
    assert sv._binom_two_sided(0, 0) == 1.0


def test_binom_two_sided_extreme_k_equals_n():
    # Regression: the most extreme result (all folds in favor) must yield the
    # smallest possible p-value 2/2^n, not 1.0 (missing min(k, n-k) mirror).
    assert sv._binom_two_sided(9, 9) == pytest.approx(2.0 / 512.0)
    assert sv._binom_two_sided(10, 10) == pytest.approx(2.0 / 1024.0)
    # symmetry: k and n-k give the same p-value
    assert sv._binom_two_sided(2, 10) == pytest.approx(sv._binom_two_sided(8, 10))
    # p-values are bounded in [0, 1] for every k
    for k in range(11):
        assert 0.0 <= sv._binom_two_sided(k, 10) <= 1.0


def test_bootstrap_ci_brackets_the_mean():
    deltas = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.015, -0.012]
    mean, lower, upper = sv._bootstrap_mean_ci(deltas, reps=500, seed=7)
    assert mean == pytest.approx(sum(deltas) / len(deltas))
    assert lower <= mean <= upper


# ---------------------------------------------------------------------------
# XOR: the pipeline must see the joint signal despite dead marginals
# ---------------------------------------------------------------------------


def test_xor_marginals_are_dead():
    marginal = _xor_result()["horizons"]["1d"]["marginal"]
    for feature in ("a", "b"):
        assert abs(marginal[feature]["oos_correlation"]) < 0.15, \
            f"marginal correlation of {feature} should be ~0, got {marginal[feature]['oos_correlation']}"


def test_xor_interaction_term_captures_the_signal():
    horizon = _xor_result()["horizons"]["1d"]
    term = horizon["interactions"]["a_x_b"]
    baseline_mse = horizon["baseline"]["mse"]
    # the product term must beat the marginal baseline substantially
    assert term["delta_mse_mean"] < -0.5 * baseline_mse
    # ...and the improvement must be significant under the Bonferroni-corrected
    # exact sign test (threshold 0.05/5 for the synthetic family of 5)
    assert term["bonferroni_threshold"] == pytest.approx(0.05 / 5)
    assert term["sign_test_p"] < term["bonferroni_threshold"]
    assert term["significant"] is True


def test_xor_joint_model_not_worse_than_single_term():
    horizon = _xor_result()["horizons"]["1d"]
    assert horizon["joint"]["delta_mse_mean"] <= horizon["interactions"]["a_x_b"]["delta_mse_mean"] + 1e-12


def test_permutation_importance_uses_feature_positions_after_intercept():
    horizon = _xor_result()["horizons"]["1d"]
    importance = horizon["permutation_importance"]
    # The interaction is the actual XOR signal. This regression assertion
    # catches an off-by-one where column zero was mapped to the intercept and
    # every reported feature importance received the previous column's value.
    assert importance["term__a_x_b"]["mean_delta_mse"] > 0.5 * horizon["baseline"]["mse"]
    assert importance["term__a_x_b"]["shuffle_scope"] == "within_walk_forward_fold"


def test_noise_case_yields_no_significant_improvement():
    horizon = _noise_result()["horizons"]["1d"]
    assert horizon["interactions"]["a_x_b"]["significant"] is False
    assert horizon["joint"]["significant"] is False
    for feature in FEATURES:
        assert horizon["ablation"][feature]["significant"] is False


# ---------------------------------------------------------------------------
# Fold boundaries mirror the parent walk-forward logic
# ---------------------------------------------------------------------------


def test_fold_splits_mirror_parent_walk_forward():
    import random
    rng = random.Random(5)
    rows = []
    day = date(2019, 1, 1)
    end = date(2025, 12, 31)
    while day <= end:
        if day.weekday() < 5:
            rows.append({"feature_date": day, "a": rng.gauss(0, 1), "b": rng.gauss(0, 1),
                         "c": rng.gauss(0, 1), "target_return_1d": rng.gauss(0, 1),
                         "target_end_1d": day + timedelta(days=1)})
        day += timedelta(days=1)
    for test_year in (2021, 2024):
        split = sv._fold_splits(rows, 1, test_year, (100, 20, 10))
        assert split is not None
        validation_start = date(test_year - 1, 1, 1)
        test_start = date(test_year, 1, 1)
        year_end = date(test_year, 12, 31)
        expected_train = [r for r in rows if r["target_end_1d"] < validation_start]
        expected_validation = [r for r in rows
                               if validation_start <= r["feature_date"] < test_start
                               and r["target_end_1d"] < test_start]
        expected_test = [r for r in rows
                         if test_start <= r["feature_date"] <= year_end
                         and r["target_end_1d"] <= year_end]
        assert len(split["selection_train"]) == len(expected_train)
        assert len(split["validation"]) == len(expected_validation)
        assert len(split["test_rows"]) == len(expected_test)


def test_fold_splits_respect_minimums():
    rows = make_xor_rows()[:300]  # too short for any fold
    assert sv._fold_splits(rows, 1, 2021, (1000, 500, 500)) is None


# ---------------------------------------------------------------------------
# Ridge plumbing (standardization + prediction round-trip)
# ---------------------------------------------------------------------------


def test_fit_variant_reproduces_train_target_on_simple_data():
    import random
    rng = random.Random(77)
    rows = []
    for day in _business_days(date(2020, 1, 1), date(2024, 12, 31)):
        x = rng.gauss(0, 1)
        rows.append({"feature_date": day, "a": x, "b": 0.0, "c": 0.0,
                     "target_return_1d": 3.0 * x + rng.gauss(0, 0.01),
                     "target_end_1d": day + timedelta(days=1)})
    split = sv._fold_splits(rows, 1, 2024, (10, 5, 5))
    assert split is not None
    model, _lambda = sv._fit_variant(split, ["a", "b", "c"], [0.1])
    train_pred = [sv._predict(model, r, ["a", "b", "c"]) for r in split["fit_rows"]]
    train_actual = [r["target_return_1d"] for r in split["fit_rows"]]
    train_mse = sum((a - p) ** 2 for a, p in zip(train_actual, train_pred)) / len(train_actual)
    assert train_mse < 0.05  # nearly perfect in-sample fit on clean synthetic data
