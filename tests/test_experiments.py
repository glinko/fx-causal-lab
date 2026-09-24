from datetime import datetime, timedelta, timezone

from fxlab.experiments import benjamini_hochberg, newey_west_mean, summarize

UTC = timezone.utc


def rows(count=10):
    result = []
    for index in range(count):
        start = datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=35*index)
        row = {"source": "bls", "event_type": "US_NFP_CHANGE", "prediction_time": start,
               "target_start_at": start + timedelta(hours=1)}
        for horizon in (1, 5, 20, 60):
            row[f"target_{horizon}d_end"] = start + timedelta(days=horizon)
            row[f"ret_{horizon}d"] = (-1 if index % 2 else 1) * .001 * (index + 1)
        result.append(row)
    return result


def test_newey_west_and_descriptive_summary():
    standard_error, statistic, p_value = newey_west_mean([.01, -.02, .03, .01, -.01], 1)
    assert standard_error > 0 and statistic != 0 and 0 <= p_value <= 1
    result = summarize(rows(), 20)
    assert result["n"] == 10 and result["inference_status"] == "diagnostic"
    assert result["hac_lags"] == 1 and result["strict_pit_eligible"] is False


def test_small_samples_do_not_claim_inference():
    result = summarize(rows(3), 1)
    assert result["inference_status"] == "insufficient_n"
    assert result["p_value"] is None and result["q_value_bh"] is None


def test_benjamini_hochberg_is_monotone_in_p_order():
    values = [{"p_value": .01, "q_value_bh": None}, {"p_value": .04, "q_value_bh": None},
              {"p_value": None, "q_value_bh": None}]
    benjamini_hochberg(values)
    assert values[0]["q_value_bh"] == .02
    assert values[1]["q_value_bh"] == .04
    assert values[2]["q_value_bh"] is None
