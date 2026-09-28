"""Side-by-side comparison contract for numeric and AnyJev predictions."""

from __future__ import annotations

import math

OPTIONS = ("down", "flat", "up")
KEYS = ("event_id", "prediction_time", "horizon_sessions")


def _key(row: dict) -> tuple:
    return tuple(row.get(name) for name in KEYS)


def _validate_prediction(row: dict, expected_track: str) -> None:
    if row.get("track") != expected_track:
        raise ValueError(f"Prediction belongs to {row.get('track')!r}, expected {expected_track!r}")
    if any(name in row for name in ("combined_score", "ensemble_score", "blended_probability")):
        raise ValueError("Parallel tracks must not contain a combined score")
    probabilities = row.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(OPTIONS):
        raise ValueError("Prediction must contain down/flat/up probabilities")
    values = [float(probabilities[name]) for name in OPTIONS]
    if not all(math.isfinite(value) and 0 <= value <= 1 for value in values) or abs(sum(values) - 1) > 1e-6:
        raise ValueError("Prediction probabilities must be finite and sum to one")
    if row.get("direction") not in OPTIONS:
        raise ValueError("Prediction direction must be down, flat or up")


def compare_parallel_predictions(quantitative: list[dict], anyjev: list[dict], actual: list[dict]) -> list[dict]:
    """Join tracks for display without averaging or feeding one into the other."""
    for row in quantitative:
        _validate_prediction(row, "quantitative")
    for row in anyjev:
        _validate_prediction(row, "anyjev")
    numeric_by_key = {_key(row): row for row in quantitative}
    anyjev_by_key = {_key(row): row for row in anyjev}
    actual_by_key = {_key(row): row for row in actual}
    shared = sorted(set(numeric_by_key) & set(anyjev_by_key) & set(actual_by_key), key=str)
    result = []
    for key in shared:
        numeric, language, outcome = numeric_by_key[key], anyjev_by_key[key], actual_by_key[key]
        actual_direction = outcome.get("actual_direction")
        if actual_direction not in OPTIONS:
            raise ValueError("Actual direction must be down, flat or up")
        numeric_correct = numeric["direction"] == actual_direction
        anyjev_correct = language["direction"] == actual_direction
        status = ("both_correct" if numeric_correct and anyjev_correct else
                  "quantitative_only" if numeric_correct else
                  "anyjev_only" if anyjev_correct else "both_wrong")
        result.append({
            **dict(zip(KEYS, key)), "quantitative_direction": numeric["direction"],
            "quantitative_probabilities": numeric["probabilities"], "anyjev_direction": language["direction"],
            "anyjev_probabilities": language["probabilities"], "actual_direction": actual_direction,
            "actual_return": outcome.get("actual_return"), "comparison": status,
        })
    return result
