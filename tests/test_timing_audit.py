"""Tests for the v0.17 timing/cutoff audit (world-snapshot publication model)."""
import json
import math
from datetime import date, timedelta

import duckdb
import pytest

from fxlab.denn import build_timing_audit
from fxlab.denn.spectral import _transform, lag_correlations


def _make_fixture(tmp_path, days=1200):
    common = tmp_path / "gold/open_daily/source/common_d1.parquet"
    common.parent.mkdir(parents=True)
    json_path = tmp_path / "common.json"
    start = date(2004, 1, 1)
    rows = []
    for index in range(days):
        day = start + timedelta(days=index)
        rows.append({
            "observation_date": str(day),
            "EURUSD_REF": 1.1 + 0.04 * math.sin(index / 37) + index * 0.000006,
            "US_2Y": 2.0 + math.sin(index / 80),
            "EA_2Y": 1.0 + math.sin(index / 90),
            "US_10Y": 3.0 + math.sin(index / 140),
            "EA_10Y": 2.0 + math.sin(index / 150),
            "BRENT": 60 + 8 * math.sin(index / 45),
            "WTI": 57 + 7 * math.sin(index / 43),
            "VIX": 20 + 3 * math.sin(index / 17),
        })
    json_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with duckdb.connect() as connection:
        connection.execute("CREATE TABLE common AS SELECT * FROM read_json_auto(?)", [str(json_path)])
        connection.execute("COPY common TO ? (FORMAT PARQUET)", [str(common)])
    (tmp_path / "reports").mkdir(exist_ok=True)
    (tmp_path / "reports" / "data_coverage.json").write_text(json.dumps({
        "dataset_id": "source", "normalized_sha256": "a" * 64,
        "generated_at": "2026-09-25T00:00:00+00:00",
        "files": {"common_d1": "gold/open_daily/source/common_d1.parquet"},
    }), encoding="utf-8")
    (tmp_path / "reports" / "denn_baseline.json").write_text(json.dumps({
        "input_dataset_id": "source", "dataset_id": "baseline",
    }), encoding="utf-8")
    return rows


def _audit_config(tmp_path, days=1200):
    """Reduced pre-registration (same protocol, fewer bootstrap replicates)."""
    config_path = tmp_path / "timing_audit.yaml"
    config_path.write_text(f"""
version: denn-timing-audit-1
candidates: [SPREAD_2Y_CHANGE, SPREAD_10Y_CHANGE, BRENT_CHANGE, WTI_CHANGE, VIX_RETURN]
timing_model:
  US_2Y:    {{published: "15:30", tz: "America/New_York", calendar_offset_days: 0, source: test}}
  US_10Y:   {{published: "15:30", tz: "America/New_York", calendar_offset_days: 0, source: test}}
  EA_2Y:    {{published: "18:50", tz: "America/New_York", calendar_offset_days: 0, source: test}}
  EA_10Y:   {{published: "18:50", tz: "America/New_York", calendar_offset_days: 0, source: test}}
  EURUSD_REF: {{published: "16:00", tz: "Europe/Berlin", calendar_offset_days: 0, source: test}}
  BRENT:    {{published: "10:30", tz: "America/New_York", calendar_offset_days: 1, source: test}}
  WTI:      {{published: "10:30", tz: "America/New_York", calendar_offset_days: 1, source: test}}
  VIX:      {{published: "15:15", tz: "America/New_York", calendar_offset_days: 0, source: test}}
candidate_nodes:
  SPREAD_2Y_CHANGE: [US_2Y, EA_2Y]
  SPREAD_10Y_CHANGE: [US_10Y, EA_10Y]
  BRENT_CHANGE: [BRENT]
  WTI_CHANGE: [WTI]
  VIX_RETURN: [VIX]
registered_lags: [1, 2, 5, 20]
exploratory_lags: [-10, 10]
cutoffs:
  - {{name: strict_07, ny_time: "07:00", description: test}}
  - {{name: before_treasury_15, ny_time: "15:00", description: test}}
  - {{name: after_us_1630, ny_time: "16:30", description: test}}
  - {{name: after_ea_1930, ny_time: "19:30", description: test}}
  - {{name: end_of_day_21, ny_time: "21:00", description: test}}
bootstrap:
  method: pair_residual_bootstrap
  replicates: 100
  seed: 20260925
  alpha: 0.05
  multiple_comparison: bonferroni_over_registered_lags
stability:
  rule: sign_consistent_in_both_chronological_halves
placebo:
  method: circular_factor_shift
  shifts_sessions: [20]
ea_sensitivity_ny_times: ["16:30", "18:50", "21:00"]
ea_sensitivity_cutoffs: [after_us_1630, after_ea_1930]
classification:
  strict_cutoff: strict_07
  robust_cutoffs: [strict_07]
  evening_cutoffs: [after_ea_1930, end_of_day_21]
""", encoding="utf-8")
    return config_path


def test_strict_cutoff_shifts_spread_by_one_grid_day(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    rows = _make_fixture(tmp_path)
    config_path = _audit_config(tmp_path)
    report = build_timing_audit(config_path)

    # Raw factor/target exactly as the v0.16 spectral baseline transforms them.
    raw = {
        "US_EA_2Y": [r["US_2Y"] - r["EA_2Y"] for r in rows],
        "VIX": [r["VIX"] for r in rows],
        "EURUSD_REF": [r["EURUSD_REF"] for r in rows],
    }
    factor = _transform(raw["US_EA_2Y"], "difference")
    vix = _transform(raw["VIX"], "log_difference")
    target = _transform(raw["EURUSD_REF"], "log_difference")

    def naive_corr(series, lag):
        return next(r["correlation"] for r in lag_correlations(series, target, 30)
                    if r["lag_sessions"] == lag)

    spread = report["candidates"]["SPREAD_2Y_CHANGE"]
    strict_lag1 = next(r["corr"] for r in spread["corrected"]["strict_07"]["per_lag"]
                       if r["lag"] == 1)
    eod_lag1 = next(r["corr"] for r in spread["corrected"]["end_of_day_21"]["per_lag"]
                    if r["lag"] == 1)

    # EA curve publishes in the NY evening -> under a 07:00 NY cutoff the latest
    # usable spread is one grid day older: corrected lag 1 == naive lag 2.
    assert strict_lag1 == pytest.approx(naive_corr(factor, 2), abs=1e-12)
    # Under the end-of-day cutoff the same-day spread is usable: corrected
    # lag 1 == naive lag 1 exactly (anchor consistency).
    assert eod_lag1 == pytest.approx(naive_corr(factor, 1), abs=1e-12)
    assert report["consistency"]["SPREAD_2Y_CHANGE"]["match"] is True

    # Same-day-published VIX: no shift under strict_07? No — 15:15 NY is after
    # 07:00 NY, so VIX also shifts by one grid day at the strict cutoff.
    vix_entry = report["candidates"]["VIX_RETURN"]
    assert next(r["corr"] for r in vix_entry["corrected"]["strict_07"]["per_lag"]
                if r["lag"] == 1) == pytest.approx(naive_corr(vix, 2), abs=1e-12)
    assert report["consistency"]["VIX_RETURN"]["match"] is True

    # EIA next-day publication: end_of_day uses a one-day-older value by design,
    # so consistency match is False (documented, not a bug).
    assert report["consistency"]["BRENT_CHANGE"]["match"] is False

    # Verdicts and files.
    for name, entry in report["candidates"].items():
        assert entry["verdict"] in {"robust", "timing_artifact", "not_reproducible"}
    assert (tmp_path / "reports" / "denn_timing_audit.json").exists()
    with duckdb.connect() as connection:
        table = str(tmp_path / report["files"]["lag_table"])
        count = connection.execute("SELECT count(*) FROM read_parquet(?)", [table]).fetchone()[0]
    # 5 candidates x (1 naive + 5 corrected) settings x 4 registered lags.
    assert count == 5 * 6 * 4


def test_bootstrap_p_is_small_for_signal_and_central_for_null():
    # Guards against the "residuals around the fitted alternative" bug: that
    # mistake centres r* at r_obs, so p ~= 0.5 even for strong signals and the
    # test never rejects. A valid H0 (slope = 0) test gives p < alpha for a
    # real signal and a central p for independent data.
    import random as _random

    from fxlab.denn.spectral import correlation
    from fxlab.denn.timing_audit import _bootstrap_p

    rng = _random.Random(7)
    xs = [float(i) for i in range(120)]
    ys = [0.5 * x + 0.1 * rng.gauss(0, 1) for x in xs]
    r_signal = correlation(xs, ys)
    p_signal = _bootstrap_p(list(zip(xs, ys)), r_signal, 300, 1)
    assert r_signal is not None and r_signal > 0.9
    assert p_signal is not None and p_signal < 0.01

    zs = [rng.gauss(0, 1) for _ in range(120)]
    r_null = correlation(xs, zs)
    p_null = _bootstrap_p(list(zip(xs, zs)), r_null, 300, 2)
    assert r_null is not None and abs(r_null) < 0.2
    assert p_null is not None and 0.05 <= p_null <= 0.95


def test_timing_audit_is_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("FXLAB_DATA", str(tmp_path))
    _make_fixture(tmp_path)
    config_path = _audit_config(tmp_path)
    first = build_timing_audit(config_path)
    second = build_timing_audit(config_path)
    assert first["dataset_id"] == second["dataset_id"]
    assert first["normalized_sha256"] == second["normalized_sha256"]
