from datetime import datetime, timedelta, timezone

import pytest

from fxlab.alignment import HORIZONS, align_event, prediction_modes, validate_alignment

UTC = timezone.utc


def event(**changes):
    value = {"event_id": "event-1", "source": "bls", "event_type": "US_CPI_ALL_MOM_SA",
             "observation_period": "2024-01", "actual": 0.3, "actual_unit": "percent",
             "reference_at": datetime(2024, 1, 2, 13, 30, tzinfo=UTC), "reference_time_kind": "published_at",
             "published_at": datetime(2024, 1, 2, 13, 30, tzinfo=UTC), "available_at": None,
             "time_quality": "unknown"}
    value.update(changes)
    return value


def market(incomplete=None):
    hourly = []
    first = datetime(2024, 1, 2, 13, tzinfo=UTC)
    for index in range(4):
        start = first + timedelta(hours=index)
        hourly.append({"bar_start": start, "bar_end": start + timedelta(hours=1), "open": 1.1 + index*.001,
                       "available_at": start + timedelta(hours=1, minutes=1)})
    daily = []
    for index in range(70):
        end = datetime(2024, 1, 2, 22, tzinfo=UTC) + timedelta(days=index)
        daily.append({"bar_end": end, "close": 1.11 + index*.001,
                      "complete": index != incomplete})
    return hourly, daily


def test_unknown_availability_emits_only_pre_event_and_starts_after_boundary():
    modes = prediction_modes(event())
    assert [row["prediction_mode"] for row in modes] == ["pre_event"]
    hourly, daily = market()
    row = align_event(event(), modes[0], hourly, daily)
    assert row["target_start_at"] == datetime(2024, 1, 2, 14, tzinfo=UTC)
    assert row["actual_feature_eligible"] is False
    assert all(row[f"ret_{horizon}d"] is not None for horizon in HORIZONS)
    assert row["target_1d_end"] > row["target_start_at"]


def test_available_event_separates_post_release_and_reaction_modes():
    available = datetime(2024, 1, 2, 13, 45, tzinfo=UTC)
    modes = prediction_modes(event(available_at=available, time_quality="inferred_conservative"))
    assert [row["prediction_mode"] for row in modes] == ["pre_event", "post_release", "reaction_confirmed"]
    assert modes[0]["actual_feature_eligible"] is False
    assert modes[1]["prediction_time"] == available
    assert modes[2]["reaction_end"] == available + timedelta(hours=1)


def test_incomplete_session_invalidates_it_and_longer_horizons_without_compression():
    hourly, daily = market(incomplete=2)
    row = align_event(event(), prediction_modes(event())[0], hourly, daily)
    assert row["target_1d_status"] == "complete"
    assert row["target_5d_status"] == "incomplete_market_path" and row["ret_5d"] is None
    assert row["target_20d_status"] == "incomplete_market_path" and row["ret_60d"] is None


def test_anti_leakage_rejects_actual_in_pre_event_row():
    hourly, daily = market()
    row = align_event(event(), prediction_modes(event())[0], hourly, daily)
    row["actual_feature_eligible"] = True
    with pytest.raises(ValueError, match="Pre-event"):
        validate_alignment(row)


def test_no_future_market_anchor_returns_none():
    hourly, daily = market()
    late = event(reference_at=datetime(2025, 1, 1, tzinfo=UTC))
    assert align_event(late, prediction_modes(late)[0], hourly, daily) is None
