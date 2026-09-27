import math
from datetime import date, timedelta

from fxlab.denn.market_forecast import _load_config, score_market_forecast


def test_market_forecast_config_freezes_market_target_and_comparisons():
    config, digest = _load_config()
    assert config["horizons"] == [1, 5, 20, 60]
    assert "Dukascopy" in config["sample"]["target"]
    assert len(config["features"]) == 16
    assert len(config["comparisons"]) == 5
    assert len(digest) == 64


def test_market_forecast_keeps_each_test_year_unseen():
    features = ["signal", "eurusd_momentum_20d"]
    rows = []
    start = date(2010, 1, 1)
    for index in range(8 * 365):
        day = start + timedelta(days=index)
        signal = math.sin(index / 19) * 0.01
        row = {"feature_date": day, "eurusd_momentum_20d": signal}
        row["signal"] = math.cos(index / 13)
        row["target_return_1d"] = signal * 0.3 + math.cos(index / 7) * 0.001
        row["target_end_1d"] = day + timedelta(days=1)
        rows.append(row)
    config = {
        "features": features, "horizons": [1], "ridge_lambdas": [0.1, 1.0],
        "minimum_train_rows": 300, "minimum_validation_rows": 100, "minimum_test_rows": 40,
        "comparisons": ["zero_return", "expanding_historical_mean", "market_momentum_ridge",
                        "full_16_feature_ridge", "elastic_net_feature_selection"],
        "elastic_net": {"alphas": [0.0001], "l1_ratios": [0.5], "max_iter": 5000,
                        "tolerance": 1e-6, "selection": "cyclic", "tie_break": "stronger_regularization"},
    }
    result, folds, predictions = score_market_forecast(rows, config)
    assert result["1d"]["models"]["market_momentum_ridge"]["skill_vs_mean"] > 0
    assert folds and predictions
    assert all(item["target_end_date"].year <= int(item["fold_id"].split("-")[0]) for item in predictions)
