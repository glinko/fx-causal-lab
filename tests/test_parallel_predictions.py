import pytest

from fxlab.denn.parallel import compare_parallel_predictions


def prediction(track, direction, probabilities):
    return {"event_id": "event-1", "prediction_time": "2020-01-01T00:00:00Z",
            "horizon_sessions": 5, "track": track, "direction": direction,
            "probabilities": probabilities}


def test_tracks_remain_separate_and_are_only_compared_after_prediction():
    numeric = prediction("quantitative", "up", {"down": 0.2, "flat": 0.2, "up": 0.6})
    anyjev = prediction("anyjev", "down", {"down": 0.7, "flat": 0.2, "up": 0.1})
    actual = [{"event_id": "event-1", "prediction_time": "2020-01-01T00:00:00Z",
               "horizon_sessions": 5, "actual_direction": "down", "actual_return": -0.01}]
    rows = compare_parallel_predictions([numeric], [anyjev], actual)
    assert rows[0]["comparison"] == "anyjev_only"
    assert rows[0]["quantitative_probabilities"] != rows[0]["anyjev_probabilities"]
    assert "combined_score" not in rows[0]


def test_combined_score_is_rejected():
    numeric = prediction("quantitative", "up", {"down": 0.2, "flat": 0.2, "up": 0.6})
    numeric["combined_score"] = 0.5
    with pytest.raises(ValueError, match="combined score"):
        compare_parallel_predictions([numeric], [], [])
