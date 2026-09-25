from datetime import datetime, timedelta, timezone

import pytest

from fxlab.interactions import positioning_snapshot, summarize_regime, trend_snapshot, validate_feature_row

UTC = timezone.utc


def test_positioning_regime_uses_only_available_expanding_history():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    positions = [{"observation_id": str(index), "available_at": start + timedelta(days=7*index),
                  "leveraged_funds_net_share_oi": index / 100, "time_quality": "inferred_conservative"}
                 for index in range(9)]
    snapshot, reason = positioning_snapshot(start + timedelta(days=49, hours=1), positions)
    assert reason is None and snapshot["feature_history_count"] == 8
    assert snapshot["feature_observation_id"] == "7" and snapshot["regime"] == "long_crowded"
    assert snapshot["feature_available_at"] <= start + timedelta(days=49, hours=1)


def test_positioning_requires_history_and_never_uses_future():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    positions = [{"observation_id": str(index), "available_at": start + timedelta(days=index),
                  "leveraged_funds_net_share_oi": index, "time_quality": "inferred_conservative"}
                 for index in range(8)]
    assert positioning_snapshot(start - timedelta(seconds=1), positions)[1] == "no_available_position"
    assert positioning_snapshot(start + timedelta(days=5), positions)[1] == "insufficient_position_history"


def test_trend_regime_stops_at_prediction_boundary():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    daily = [{"session_date": f"d{index}", "close": 1 + index/100,
              "available_at": start + timedelta(days=index), "complete": True,
              "time_quality": "inferred_conservative"} for index in range(22)]
    snapshot, reason = trend_snapshot(start + timedelta(days=20, hours=1), daily)
    assert reason is None and snapshot["regime"] == "eur_uptrend"
    assert snapshot["feature_observation_id"] == "d20"
    assert snapshot["feature_history_count"] == 21


def test_feature_validation_and_summary_are_descriptive_only():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    base = {"feature_name": "fx_trend_20d", "source": "bls", "event_type": "US_NFP_CHANGE",
            "regime": "eur_uptrend", "feature_available_at": start, "prediction_time": start,
            "target_start_at": start + timedelta(hours=1), "strict_pit_eligible": False}
    rows = []
    for index in range(3):
        row = {**base, "target_start_at": start + timedelta(days=index), "ret_1d": .01 * (index - 1),
               "target_1d_end": start + timedelta(days=index + 1)}
        rows.append(row)
    validate_feature_row({**rows[0], **{f"target_{h}d_end": start + timedelta(days=h) for h in (5, 20, 60)}})
    result = summarize_regime(rows, 1)
    assert result["n"] == 3 and result["inference_status"] == "descriptive_non_strict"
    assert "p_value" not in result and result["sample_warning"] == "small_sample"
    with pytest.raises(ValueError, match="after prediction_time"):
        validate_feature_row({**rows[0], "feature_available_at": start + timedelta(seconds=1)})
