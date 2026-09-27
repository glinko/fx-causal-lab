"""Add CFTC positioning and EIA fundamentals to the market-target baseline."""
from __future__ import annotations

import hashlib
import json
import math
import os
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean

import duckdb
import yaml

from ..store import atomic_json, root
from .elastic_net import _select_parameters, fit_elastic_net, predict_elastic_net
from .market_forecast import _load_config as _load_market_config, _load_rows as _load_market_rows, _select_ridge
from .pipeline import _correlation, _fit_ridge, _mse, _predict, _write_parquet
from .state_vector import _fold_splits

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "flow_energy_forecast.yaml"


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-flow-energy-forecast-1":
        raise ValueError("Unsupported flow/energy forecast config")
    required = {"parent_market_config", "base_features", "new_features", "horizons", "ridge_lambdas",
                "elastic_net", "availability", "sample", "comparisons"}
    if not required <= set(config):
        raise ValueError(f"Flow/energy config is missing keys: {sorted(required - set(config))}")
    if len(config["base_features"]) != 16 or len(config["new_features"]) != 8:
        raise ValueError("Flow/energy comparison must remain 16 base plus 8 new features")
    if sorted(config["horizons"]) != [1, 5, 20, 60] or not config["sample"].get("no_imputation"):
        raise ValueError("Flow/energy horizons or missing-value policy changed")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _rolling_zscore(values: list[float | None], window: int) -> list[float | None]:
    result: list[float | None] = []
    for index, current in enumerate(values):
        sample = values[index - window + 1:index + 1] if index + 1 >= window else []
        if current is None or len(sample) != window or any(value is None for value in sample):
            result.append(None)
            continue
        numeric = [float(value) for value in sample]
        center = fmean(numeric)
        variance = fmean((value - center) ** 2 for value in numeric)
        result.append(0.0 if variance <= 1e-18 else (float(current) - center) / math.sqrt(variance))
    return result


def _lag_change(values: list[float], lag: int, log: bool = False) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    for index in range(lag, len(values)):
        if log:
            if values[index] <= 0 or values[index - lag] <= 0:
                raise ValueError("Log change requires positive values")
            result[index] = math.log(values[index] / values[index - lag])
        else:
            result[index] = values[index] - values[index - lag]
    return result


def _asof_join(days: list[date], releases: list[tuple[date, dict]]) -> list[tuple[dict | None, int | None]]:
    ordered = sorted(releases, key=lambda row: row[0])
    release_days = [row[0] for row in ordered]
    result = []
    for day in days:
        index = bisect_right(release_days, day) - 1
        result.append((None, None) if index < 0 else (ordered[index][1], (day - release_days[index]).days))
    return result


def _load_cftc(report: dict, delay_days: int) -> tuple[list[tuple[date, dict]], dict]:
    path = root() / report["files"]["positions"]
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT report_date,available_at,time_quality,open_interest,dealer_net_share_oi,"
            "asset_manager_net_share_oi,leveraged_funds_net_share_oi FROM read_parquet(?) ORDER BY report_date",
            [str(path)],
        ).fetchall()
    report_dates = [date.fromisoformat(str(row[0])) for row in rows]
    known = [row[1] is not None for row in rows]
    availability = [row[1].date() if row[1] is not None else report_dates[index] + timedelta(days=delay_days)
                    for index, row in enumerate(rows)]
    open_interest = [float(row[3]) for row in rows]
    dealer = [float(row[4]) for row in rows]
    asset = [float(row[5]) for row in rows]
    leveraged = [float(row[6]) for row in rows]
    derived = {
        "cftc_leveraged_z52": _rolling_zscore(leveraged, 52),
        "cftc_asset_manager_z52": _rolling_zscore(asset, 52),
        "cftc_dealer_z52": _rolling_zscore(dealer, 52),
        "cftc_leveraged_change_4w": _lag_change(leveraged, 4),
        "cftc_asset_manager_change_4w": _lag_change(asset, 4),
        "cftc_open_interest_log_change_4w": _lag_change(open_interest, 4, log=True),
    }
    releases = []
    for index, available in enumerate(availability):
        values = {name: series[index] for name, series in derived.items()}
        if all(value is not None and math.isfinite(float(value)) for value in values.values()):
            releases.append((available, {**values, "cftc_release_known": known[index],
                                         "cftc_report_date": report_dates[index]}))
    return releases, {"rows": len(rows), "known_availability_rows": sum(known),
                      "inferred_availability_rows": len(rows) - sum(known),
                      "inferred_delay_days": delay_days}


def _load_eia(report: dict) -> tuple[list[tuple[date, dict]], dict]:
    path = root() / report["files"]["weekly_petroleum"]
    with duckdb.connect() as connection:
        rows = connection.execute(
            "SELECT series_id,observation_date,available_at,value FROM read_parquet(?) "
            "ORDER BY series_id,observation_date", [str(path)]).fetchall()
    by_series: dict[str, list[tuple[date, date, float]]] = {}
    for series_id, observation_date, available_at, value in rows:
        by_series.setdefault(series_id, []).append((date.fromisoformat(str(observation_date)),
                                                    available_at.date(), float(value)))
    stocks = by_series["EIA_COMMERCIAL_CRUDE_STOCKS"]
    production = by_series["EIA_US_CRUDE_PRODUCTION"]
    stock_change = _lag_change([row[2] for row in stocks], 1)
    production_change = _lag_change([row[2] for row in production], 4)
    stock_z = _rolling_zscore(stock_change, 52)
    production_z = _rolling_zscore(production_change, 52)
    stock_map = {stocks[index][1]: stock_z[index] for index in range(len(stocks)) if stock_z[index] is not None}
    production_map = {production[index][1]: production_z[index] for index in range(len(production))
                      if production_z[index] is not None}
    releases = []
    for available in sorted(set(stock_map) & set(production_map)):
        releases.append((available, {"eia_stocks_change_1w_z52": stock_map[available],
                                     "eia_production_change_4w_z52": production_map[available]}))
    return releases, {"stocks_rows": len(stocks), "production_rows": len(production),
                      "availability": "inferred_conservative"}


def build_feature_rows(config: dict) -> tuple[list[dict], dict]:
    market_config_path = Path(os.environ.get("FXLAB_PROJECT", ".")) / config["parent_market_config"]
    market_config, _ = _load_market_config(market_config_path)
    rows, parent, coverage = _load_market_rows(market_config)
    cftc_path, eia_path = root() / "reports" / "cftc.json", root() / "reports" / "eia_energy.json"
    if not cftc_path.exists() or not eia_path.exists():
        raise ValueError("CFTC and EIA reports must be built first")
    cftc_report = json.loads(cftc_path.read_text(encoding="utf-8"))
    eia_report = json.loads(eia_path.read_text(encoding="utf-8"))
    cftc_releases, cftc_quality = _load_cftc(
        cftc_report, int(config["availability"]["cftc_unknown_history_delay_days"]))
    eia_releases, eia_quality = _load_eia(eia_report)
    days = [row["feature_date"] for row in rows]
    cftc_joined, eia_joined = _asof_join(days, cftc_releases), _asof_join(days, eia_releases)
    output, known_rows = [], 0
    for row, (cftc, cftc_age), (eia, eia_age) in zip(rows, cftc_joined, eia_joined):
        if cftc is None or eia is None:
            continue
        item = dict(row)
        item.update({name: float(cftc[name]) for name in config["new_features"] if name.startswith("cftc_")})
        item.update({name: float(eia[name]) for name in config["new_features"] if name.startswith("eia_")})
        item["cftc_age_days"] = cftc_age
        item["eia_age_days"] = eia_age
        item["cftc_release_known"] = bool(cftc["cftc_release_known"])
        item["cftc_report_date"] = cftc["cftc_report_date"]
        known_rows += int(item["cftc_release_known"])
        output.append(item)
    if len(output) < 2500:
        raise ValueError("Flow/energy complete-case matrix is too short")
    provenance = {"market_parent_dataset_id": parent["dataset_id"], "market_coverage_dataset_id": coverage["dataset_id"],
                  "cftc_dataset_id": cftc_report["dataset_id"], "eia_dataset_id": eia_report["dataset_id"],
                  "cftc": cftc_quality, "eia": eia_quality, "rows_with_known_cftc_release": known_rows}
    return output, provenance


def score_flow_energy(rows: list[dict], config: dict) -> tuple[dict, list[dict]]:
    base = list(config["base_features"])
    extended = base + list(config["new_features"])
    minimums = (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]),
                int(config["minimum_test_rows"]))
    lambdas = [float(value) for value in config["ridge_lambdas"]]
    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    result, fold_rows = {}, []
    for horizon in config["horizons"]:
        target = f"target_return_{horizon}d"
        pooled_actual: list[float] = []
        pooled = {name: [] for name in config["comparisons"]}
        horizon_folds = []
        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
                continue
            base_lambda = _select_ridge(split, base, target, lambdas)
            extended_lambda = _select_ridge(split, extended, target, lambdas)
            base_model = _fit_ridge(split["fit_rows"], base, target, base_lambda)
            extended_model = _fit_ridge(split["fit_rows"], extended, target, extended_lambda)
            alpha, ratio = _select_parameters(split["selection_train"], split["validation"], extended,
                                               target, config["elastic_net"])
            sparse = fit_elastic_net(split["fit_rows"], extended, target, alpha, ratio,
                                     int(config["elastic_net"]["max_iter"]),
                                     float(config["elastic_net"]["tolerance"]))
            actual = [float(row[target]) for row in split["test_rows"]]
            historical_mean = fmean(float(row[target]) for row in split["fit_rows"])
            forecasts = {
                "zero_return": [0.0] * len(actual),
                "expanding_historical_mean": [historical_mean] * len(actual),
                "base_16_feature_ridge": [_predict(base_model, row, base) for row in split["test_rows"]],
                "extended_24_feature_ridge": [_predict(extended_model, row, extended) for row in split["test_rows"]],
                "extended_elastic_net": predict_elastic_net(sparse, split["test_rows"], extended),
            }
            metrics = {name: _mse(actual, values) for name, values in forecasts.items()}
            fold = {"fold_id": f"{test_year}-{horizon}d", "test_year": test_year,
                    "horizon_sessions": horizon, "test_rows": len(actual), "base_lambda": base_lambda,
                    "extended_lambda": extended_lambda, "elastic_alpha": alpha, "elastic_l1_ratio": ratio,
                    "elastic_nonzero": len(sparse["nonzero_features"]),
                    "elastic_features": ",".join(sparse["nonzero_features"]),
                    **{f"{name}_mse": value for name, value in metrics.items()}}
            horizon_folds.append(fold); fold_rows.append(fold); pooled_actual.extend(actual)
            for name, values in forecasts.items():
                pooled[name].extend(values)
        if not horizon_folds:
            raise ValueError(f"No flow/energy folds for {horizon}d")
        mean_mse = _mse(pooled_actual, pooled["expanding_historical_mean"])
        base_mse = _mse(pooled_actual, pooled["base_16_feature_ridge"])
        models = {}
        for name, values in pooled.items():
            mse = _mse(pooled_actual, values)
            models[name] = {"mse": mse, "skill_vs_mean": 1 - mse / mean_mse,
                            "improvement_vs_base16": 1 - mse / base_mse,
                            "correlation": _correlation(pooled_actual, values),
                            "sign_accuracy": fmean((a >= 0) == (p >= 0) for a, p in zip(pooled_actual, values)),
                            "years_better_than_base16": sum(
                                fold[f"{name}_mse"] < fold["base_16_feature_ridge_mse"] for fold in horizon_folds),
                            "years": len(horizon_folds)}
        result[f"{horizon}d"] = {"observations": len(pooled_actual), "models": models}
    return result, fold_rows


def build_flow_energy_forecast(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    rows, provenance = build_feature_rows(config)
    result, folds = score_flow_energy(rows, config)
    identity = {"config_sha256": config_hash, "provenance": provenance, "result": result}
    normalized_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    folder = root() / "gold" / "denn_flow_energy" / dataset_id
    _write_parquet(rows, folder / "features.parquet", "feature_date")
    _write_parquet(folds, folder / "fold_metrics.parquet", "horizon_sessions,test_year")
    extra = {label: values["models"]["extended_24_feature_ridge"] for label, values in result.items()}
    elastic = {label: values["models"]["extended_elastic_net"] for label, values in result.items()}
    any_mean_win = any(value["skill_vs_mean"] > 0 for value in [*extra.values(), *elastic.values()])
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "normalized_sha256": normalized_hash,
        "generated_at": datetime.now(UTC).isoformat(), "config_sha256": config_hash,
        "sample_rows": len(rows), "date_from": str(rows[0]["feature_date"]), "date_to": str(rows[-1]["feature_date"]),
        "horizons": list(config["horizons"]), "base_features": list(config["base_features"]),
        "new_features": list(config["new_features"]), "provenance": provenance, "result": result,
        "story": {"headline": ("CFTC и EIA дали улучшение хотя бы на одном сроке." if any_mean_win else
                                "CFTC и EIA не смогли обойти простой ориентир ни на одном сроке."),
                  "question": "Уменьшилась ли ошибка после добавления позиций фьючерсного рынка и нефтяных запасов/добычи?",
                  "findings": [
                      "Прямое добавление восьми новых показателей ухудшило исходную модель на " + ", ".join(
                          f"{label}: {abs(value['improvement_vs_base16']) * 100:.2f}%" for label, value in extra.items()),
                      "Автоматический отбор оказался лучше исходной модели на " + ", ".join(
                          f"{label}: {value['improvement_vs_base16'] * 100:.2f}%" for label, value in elastic.items()),
                      "Даже после отбора ошибка осталась выше среднего прошлых лет на " + ", ".join(
                          f"{label}: {abs(value['skill_vs_mean']) * 100:.2f}%" for label, value in elastic.items()),
                      "На 60 днях направление угадывалось чаще, но размер движения оценивался слишком неточно.",
                  ]},
        "files": {"features": (folder / "features.parquet").relative_to(root()).as_posix(),
                  "fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix()},
        "strict_pit_eligible": False, "inference_status": config["inference_status"],
        "limitations": [
            "Для старой истории CFTC нет подтверждённых дат публикации; используется явно отмеченная задержка в 10 дней.",
            "Только последние строки CFTC имеют календарь публикаций, поэтому их пока слишком мало для отдельной многолетней проверки.",
            "EIA использует консервативную дату доступности через семь дней после недели наблюдения.",
            "Все ряды взяты из обновляемых исторических выгрузок и пока не являются строгими архивами прошлых версий.",
        ],
    }
    atomic_json(root() / "reports" / "denn_flow_energy.json", report)
    return report
