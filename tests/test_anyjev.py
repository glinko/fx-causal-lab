from datetime import datetime, timedelta, timezone

import pytest

from fxlab.denn.anyjev import build_anyjev_job, load_anyjev_config, render_world_state, return_bucket


def snapshot():
    prediction_time = datetime(2026, 9, 27, 21, 0, tzinfo=timezone.utc)
    return {
        "prediction_time": prediction_time,
        "available_at_max": prediction_time - timedelta(minutes=1),
        "point_in_time_status": "non_strict_current_history",
        "strict_pit_eligible": False,
        "features": {"spread_2y_z60": 0.5, "vix_z60": -0.25},
    }


def test_anyjev_config_and_job_are_stable():
    config, digest = load_anyjev_config()
    first = build_anyjev_job(snapshot(), "fx_return_5d_v1", config, digest)
    second = build_anyjev_job(snapshot(), "fx_return_5d_v1", config, digest)
    assert first == second
    assert first["options"] == ["down", "flat", "up"]
    assert first["horizon_sessions"] == 5
    assert len(first["state_sha256"]) == 64 and len(first["job_id"]) == 24
    assert "US minus euro-area 2Y yield spread" in first["state"]
    assert "Gold change over 20 sessions [log return]: missing" in first["state"]


def test_anyjev_state_rejects_future_or_non_finite_values():
    config, _ = load_anyjev_config()
    future = snapshot()
    future["available_at_max"] = future["prediction_time"] + timedelta(seconds=1)
    with pytest.raises(ValueError, match="not available"):
        render_world_state(future, config)
    invalid = snapshot()
    invalid["features"]["spread_2y_z60"] = float("nan")
    with pytest.raises(ValueError, match="Non-finite"):
        render_world_state(invalid, config)


def test_return_bucket_uses_frozen_past_fold_bounds():
    assert return_bucket(-0.02, -0.01, 0.01) == 0
    assert return_bucket(0.0, -0.01, 0.01) == 1
    assert return_bucket(0.02, -0.01, 0.01) == 2
    with pytest.raises(ValueError):
        return_bucket(0.0, 0.01, -0.01)

