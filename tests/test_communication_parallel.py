from datetime import date, datetime, timedelta, timezone

from fxlab.denn.communication_parallel import build_anyjev_jobs, score_quantitative


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
    predictions = [{"event_id": row["event_id"], "horizon_sessions": horizon} for horizon in (1, 5, 20, 60)]
    jobs = build_anyjev_jobs([row], predictions)
    assert len(jobs) == 4
    serialized = str(jobs)
    assert "actual_return" not in serialized
    assert "forecast_return" not in serialized
    assert all(job["actual_not_in_job"] is True and job["track"] == "anyjev" for job in jobs)
