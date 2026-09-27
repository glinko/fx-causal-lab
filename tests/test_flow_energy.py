from datetime import date

from fxlab.denn.flow_energy import _asof_join, _lag_change, _load_config, _rolling_zscore


def test_flow_energy_config_keeps_frozen_comparison():
    config, digest = _load_config()
    assert len(config["base_features"]) == 16
    assert len(config["new_features"]) == 8
    assert config["horizons"] == [1, 5, 20, 60]
    assert config["availability"]["cftc_unknown_history_delay_days"] == 10
    assert len(digest) == 64


def test_weekly_values_only_appear_on_or_after_availability_day():
    releases = [(date(2024, 1, 5), {"value": 1.0}), (date(2024, 1, 12), {"value": 2.0})]
    joined = _asof_join([date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 11), date(2024, 1, 12)], releases)
    assert joined == [(None, None), ({"value": 1.0}, 0), ({"value": 1.0}, 6), ({"value": 2.0}, 0)]


def test_weekly_transforms_use_only_past_observations():
    assert _lag_change([100.0, 110.0, 121.0], 1, log=True)[1:] == [
        __import__("math").log(1.1), __import__("math").log(1.1)]
    values = [float(index) for index in range(52)]
    scored = _rolling_zscore(values, 52)
    assert all(value is None for value in scored[:51]) and scored[51] > 0
