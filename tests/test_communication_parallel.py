from datetime import date, datetime, timedelta, timezone

import json

from fxlab.denn.communication_parallel import (build_anyjev_jobs, import_anyjev_l2,
                                                import_anyjev_pilot, score_quantitative)
from fxlab.denn.pipeline import _write_parquet


def rows():
    result = []
    for year in range(2010, 2021):
        for index in range(30):
            day = date(year, 1, 1) + timedelta(days=index * 10)
            signal = ((index % 7) - 3) / 10
            row = {"event_id": f"{year}-{index}", "feature_date": day,
                   "prediction_time": datetime.combine(day, datetime.min.time(), timezone.utc),
                   "x": signal, "full_text": "A policy statement"}
            for horizon in (1, 5, 20, 60):
                row[f"target_return_{horizon}d"] = signal * 0.01
                row[f"target_end_{horizon}d"] = day + timedelta(days=horizon)
            result.append(row)
    return result


def test_numeric_track_produces_frozen_independent_predictions():
    config = {"features": ["x"], "horizons": [1, 5, 20, 60], "ridge_lambdas": [0.1, 1.0],
              "minimum_train_rows": 60, "minimum_validation_rows": 20, "minimum_test_rows": 20,
              "tracks": {"quantitative": "numeric-test"}}
    result, predictions = score_quantitative(rows(), config)
    assert set(result) == {"1d", "5d", "20d", "60d"}
    assert predictions and all(row["track"] == "quantitative" for row in predictions)
    assert all("anyjev" not in key for row in predictions for key in row)
    assert all(abs(row["probability_down"] + row["probability_flat"] + row["probability_up"] - 1) < 1e-9
               for row in predictions)


def test_anyjev_jobs_contain_neither_actual_outcome_nor_numeric_prediction():
    row = rows()[-1]
    feature_ids = ["spread_2y_z60", "spread_10y_z60", "brent_asinh_change_20d",
                   "wti_asinh_change_20d", "vix_z60", "eurusd_momentum_20d", "bk5_z60", "bk10_z60",
                   "nfci_z60", "anfci_z60", "h41_tga_ratio_z252", "h41_rrp_ratio_z252",
                   "h41_reserves_ratio_z252", "spx_ret_20d", "eu_ret_20d", "gold_ret_20d"]
    row.update({name: 0.1 for name in feature_ids})
    predictions = [{"event_id": row["event_id"], "horizon_sessions": horizon,
                    "lower_bound": -0.01, "upper_bound": 0.01} for horizon in (1, 5, 20, 60)]
    jobs = build_anyjev_jobs([row], predictions)
    assert len(jobs) == 4
    serialized = str(jobs)
    assert "actual_return" not in serialized
    assert "forecast_return" not in serialized
    assert all(job["actual_not_in_job"] is True and job["track"] == "anyjev" for job in jobs)
    assert all(job["label_bounds"] == {"down_below": -0.01, "up_above": 0.01} for job in jobs)


def test_pilot_import_compares_but_does_not_combine(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    numeric_path = tmp_path/"gold/numeric.parquet"
    _write_parquet([{"event_id": "event-1", "prediction_time": "2020-01-01T00:00:00Z",
                     "horizon_sessions": 5, "direction": "up", "probability_down": 0.1,
                     "probability_flat": 0.2, "probability_up": 0.7, "actual_return": -0.01,
                     "actual_direction": "down"}], numeric_path, "event_id")
    (tmp_path/"reports").mkdir()
    (tmp_path/"reports/communication_parallel.json").write_text(json.dumps({
        "dataset_id": "parent", "files": {"quantitative_predictions": "gold/numeric.parquet"}}))
    response_path = tmp_path/"response.jsonl"
    response_path.write_text(json.dumps({"job_id": "job-1", "event_id": "event-1",
        "prediction_time": "2020-01-01T00:00:00Z", "horizon_sessions": 5, "track": "anyjev",
        "direction": "down", "probabilities": {"down": 0.8, "flat": 0.1, "up": 0.1},
        "actual_not_in_request": True, "state_chars_total": 4000, "state_chars_used": 3500,
        "elapsed_seconds": 2.0}) + "\n")
    report = import_anyjev_pilot(response_path)
    assert report["counts"]["anyjev_only"] == 1
    assert report["combined_score"] is False
    assert report["anyjev_direction_counts"] == {"down": 1, "flat": 0, "up": 0}
    assert report["verdict"] == "degenerate_constant_prediction"


def test_l2_import_keeps_tracks_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    numeric_path = tmp_path/"gold/numeric.parquet"
    _write_parquet([{"event_id": "event-1", "prediction_time": "2023-01-01T00:00:00Z",
                     "horizon_sessions": 5, "direction": "up", "probability_down": 0.1,
                     "probability_flat": 0.2, "probability_up": 0.7, "actual_return": -0.01,
                     "actual_direction": "down"}], numeric_path, "event_id")
    (tmp_path/"reports").mkdir()
    (tmp_path/"reports/communication_parallel.json").write_text(json.dumps({
        "dataset_id": "parent", "files": {"quantitative_predictions": "gold/numeric.parquet"}}))
    (tmp_path/"reports/communication_anyjev_l2_bundle.json").write_text(json.dumps({
        "dataset_id": "bundle", "bundle_sha256": "bundle-hash", "config_sha256": "config-hash"}))
    result_path = tmp_path/"result.json"
    result_path.write_text(json.dumps({
        "schema_version": "fxlab-anyjev-l2-result-1", "status": "complete",
        "bundle_sha256": "bundle-hash", "config_sha256": "config-hash",
        "numeric_prediction_in_state": False,
        "selection_scheme": "past_train_then_validation_then_later_test",
        "model_id": "Qwen3-8B-b24", "split_rows": {"train": 10, "validation": 3, "test": 1},
        "test_majority_class_accuracy": 1.0, "test_log_loss": 0.2, "test_brier": 0.1,
        "selected_lambda": 1.0, "selected_temperature": 1.0, "elapsed_seconds": 3.0,
        "predictions": [{"job_id": "job-1", "event_id": "event-1",
                         "horizon_sessions": 5, "track": "anyjev", "level": "L2", "split": "test",
                         "direction": "down", "probabilities": {"down": 0.8, "flat": 0.1, "up": 0.1},
                         "actual_not_in_request": True}],
    }))
    report = import_anyjev_l2(result_path)
    assert report["counts"]["anyjev_only"] == 1
    assert report["combined_score"] is False
    assert report["numeric_prediction_in_state"] is False
