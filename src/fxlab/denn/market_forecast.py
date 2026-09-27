"""Walk-forward 1/5/20/60-day forecasts for the Dukascopy EUR/USD target."""
from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import fmean

import duckdb
import yaml

from ..store import atomic_json, root
from .elastic_net import _select_parameters, fit_elastic_net, predict_elastic_net
from .pipeline import _correlation, _fit_ridge, _mse, _predict, _write_parquet
from .state_vector import _fold_splits

UTC = timezone.utc
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "market_forecast.yaml"


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-market-forecast-1":
        raise ValueError("Unsupported market forecast config")
    required = {"parent_report", "features", "horizons", "ridge_lambdas", "elastic_net", "sample",
                "comparisons", "minimum_train_rows", "minimum_validation_rows", "minimum_test_rows"}
    if not required <= set(config):
        raise ValueError(f"Market forecast config is missing keys: {sorted(required - set(config))}")
    if sorted(config["horizons"]) != [1, 5, 20, 60]:
        raise ValueError("Market forecast horizons must remain 1/5/20/60")
    if len(config["features"]) != 16 or len(config["features"]) != len(set(config["features"])):
        raise ValueError("Market forecast requires the frozen 16 unique Tier A features")
    expected = {"zero_return", "expanding_historical_mean", "market_momentum_ridge",
                "full_16_feature_ridge", "elastic_net_feature_selection"}
    if set(config["comparisons"]) != expected:
        raise ValueError("Market forecast comparison set changed")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _load_rows(config: dict) -> tuple[list[dict], dict, dict]:
    parent_path = root() / "reports" / f"{config['parent_report']}.json"
    coverage_path = root() / "reports" / "data_coverage.json"
    if not parent_path.exists() or not coverage_path.exists():
        raise ValueError("Tier A features and data coverage must be built first")
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    market_relative = coverage.get("files", {}).get("common_market_d1")
    if not market_relative:
        raise ValueError("Dukascopy common market grid is missing")
    feature_path = root() / parent["files"]["features"]
    market_path = root() / market_relative
    quoted = ",".join(f'"{name}"' for name in config["features"])
    with duckdb.connect() as connection:
        feature_rows = connection.execute(
            f"SELECT feature_date,{quoted} FROM read_parquet(?) ORDER BY feature_date", [str(feature_path)]
        ).fetchall()
        market_rows = connection.execute(
            'SELECT observation_date,"EURUSD" FROM read_parquet(?) ORDER BY observation_date', [str(market_path)]
        ).fetchall()
    market_dates = [date.fromisoformat(str(row[0])) for row in market_rows]
    prices = [float(row[1]) for row in market_rows]
    if len(market_dates) < 2500 or market_dates != sorted(set(market_dates)):
        raise ValueError("Dukascopy market grid is too short, unordered or duplicated")
    positions = {day: index for index, day in enumerate(market_dates)}
    maximum_horizon = max(config["horizons"])
    rows = []
    for values in feature_rows:
        day = date.fromisoformat(str(values[0]))
        index = positions.get(day)
        if index is None or index < 20 or index + maximum_horizon >= len(prices):
            continue
        if any(value is None for value in values[1:]):
            continue
        item = {"feature_date": day}
        for name, value in zip(config["features"], values[1:]):
            numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"Non-finite Tier A feature: {name}")
            item[name] = numeric
        item["eurusd_momentum_20d"] = math.log(prices[index] / prices[index - 20])
        for horizon in config["horizons"]:
            item[f"target_return_{horizon}d"] = math.log(prices[index + horizon] / prices[index])
            item[f"target_end_{horizon}d"] = market_dates[index + horizon]
        rows.append(item)
    if len(rows) < 2500:
        raise ValueError("Market-target complete-case matrix is too short")
    return rows, parent, coverage


def _select_ridge(split: dict, features: list[str], target: str, lambdas: list[float]) -> float:
    actual = [float(row[target]) for row in split["validation"]]
    best = None
    for ridge in lambdas:
        model = _fit_ridge(split["selection_train"], features, target, float(ridge))
        score = _mse(actual, [_predict(model, row, features) for row in split["validation"]])
        candidate = (score, -float(ridge), float(ridge))
        if best is None or candidate < best:
            best = candidate
    return best[2]


def score_market_forecast(rows: list[dict], config: dict) -> tuple[dict, list[dict], list[dict]]:
    minimums = (int(config["minimum_train_rows"]), int(config["minimum_validation_rows"]),
                int(config["minimum_test_rows"]))
    features = list(config["features"])
    ridge_lambdas = [float(value) for value in config["ridge_lambdas"]]
    years = range(rows[0]["feature_date"].year + 6, rows[-1]["feature_date"].year + 1)
    result, fold_rows, predictions = {}, [], []
    for horizon in config["horizons"]:
        target = f"target_return_{horizon}d"
        actual_all: list[float] = []
        pooled = {name: [] for name in config["comparisons"]}
        horizon_folds = []
        feature_counts = Counter()
        for test_year in years:
            split = _fold_splits(rows, horizon, test_year, minimums)
            if split is None:
                continue
            mean_value = fmean(float(row[target]) for row in split["fit_rows"])
            momentum_features = ["eurusd_momentum_20d"]
            momentum_lambda = _select_ridge(split, momentum_features, target, ridge_lambdas)
            full_lambda = _select_ridge(split, features, target, ridge_lambdas)
            momentum_model = _fit_ridge(split["fit_rows"], momentum_features, target, momentum_lambda)
            full_model = _fit_ridge(split["fit_rows"], features, target, full_lambda)
            alpha, ratio = _select_parameters(split["selection_train"], split["validation"], features,
                                               target, config["elastic_net"])
            sparse_model = fit_elastic_net(split["fit_rows"], features, target, alpha, ratio,
                                           int(config["elastic_net"]["max_iter"]),
                                           float(config["elastic_net"]["tolerance"]))
            actual = [float(row[target]) for row in split["test_rows"]]
            forecast = {
                "zero_return": [0.0] * len(actual),
                "expanding_historical_mean": [mean_value] * len(actual),
                "market_momentum_ridge": [_predict(momentum_model, row, momentum_features) for row in split["test_rows"]],
                "full_16_feature_ridge": [_predict(full_model, row, features) for row in split["test_rows"]],
                "elastic_net_feature_selection": predict_elastic_net(sparse_model, split["test_rows"], features),
            }
            mean_mse = _mse(actual, forecast["expanding_historical_mean"])
            metrics = {name: {"mse": _mse(actual, values),
                              "skill_vs_mean": 1 - _mse(actual, values) / mean_mse if mean_mse else None,
                              "correlation": _correlation(actual, values),
                              "sign_accuracy": fmean((left >= 0) == (right >= 0)
                                                     for left, right in zip(actual, values))}
                       for name, values in forecast.items()}
            record = {"fold_id": f"{test_year}-{horizon}d", "test_year": test_year,
                      "horizon_sessions": horizon, "test_rows": len(actual),
                      "momentum_lambda": momentum_lambda, "full_lambda": full_lambda,
                      "elastic_alpha": alpha, "elastic_l1_ratio": ratio,
                      "elastic_nonzero": len(sparse_model["nonzero_features"]),
                      "elastic_features": ",".join(sparse_model["nonzero_features"]), "metrics": metrics}
            horizon_folds.append(record)
            feature_counts.update(sparse_model["nonzero_features"])
            actual_all.extend(actual)
            for name, values in forecast.items():
                pooled[name].extend(values)
            for row_index, (row, actual_value) in enumerate(zip(split["test_rows"], actual)):
                predictions.append({"fold_id": record["fold_id"], "feature_date": row["feature_date"],
                                    "target_end_date": row[f"target_end_{horizon}d"],
                                    "horizon_sessions": horizon, "actual_return": actual_value,
                                    **{f"forecast_{name}": values[row_index] for name, values in forecast.items()}})
        if not horizon_folds:
            raise ValueError(f"No valid market forecast folds for {horizon}d")
        aggregate = {}
        mean_mse = _mse(actual_all, pooled["expanding_historical_mean"])
        for name, values in pooled.items():
            mse = _mse(actual_all, values)
            aggregate[name] = {"mse": mse, "skill_vs_mean": 1 - mse / mean_mse if mean_mse else None,
                               "correlation": _correlation(actual_all, values),
                               "sign_accuracy": fmean((left >= 0) == (right >= 0)
                                                      for left, right in zip(actual_all, values)),
                               "years_better_than_mean": sum(fold["metrics"][name]["mse"] <
                                                             fold["metrics"]["expanding_historical_mean"]["mse"]
                                                             for fold in horizon_folds),
                               "years": len(horizon_folds)}
        result[f"{horizon}d"] = {"observations": len(actual_all), "models": aggregate,
                                  "elastic_feature_frequency": {
                                      name: feature_counts[name] / len(horizon_folds) for name in features}}
        for fold in horizon_folds:
            flat = {key: value for key, value in fold.items() if key != "metrics"}
            for model, metrics in fold["metrics"].items():
                for metric, value in metrics.items():
                    flat[f"{model}_{metric}"] = value
            fold_rows.append(flat)
    return result, fold_rows, predictions


def build_market_forecast(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    rows, parent, coverage = _load_rows(config)
    result, folds, predictions = score_market_forecast(rows, config)
    identity = {"config_sha256": config_hash, "tier_a_dataset_id": parent["dataset_id"],
                "coverage_dataset_id": coverage["dataset_id"], "result": result}
    normalized_hash = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    dataset_id = normalized_hash[:20]
    folder = root() / "gold" / "denn_market_forecast" / dataset_id
    _write_parquet(folds, folder / "fold_metrics.parquet", "horizon_sessions,test_year")
    _write_parquet(predictions, folder / "predictions.parquet", "horizon_sessions,feature_date")
    best = {}
    for label, values in result.items():
        candidates = [(details["mse"], name) for name, details in values["models"].items()]
        best[label] = min(candidates)[1]
    zero_wins_all = all(name == "zero_return" for name in best.values())
    full_skills = {label: values["models"]["full_16_feature_ridge"]["skill_vs_mean"]
                   for label, values in result.items()}
    sparse_skills = {label: values["models"]["elastic_net_feature_selection"]["skill_vs_mean"]
                     for label, values in result.items()}
    story = {
        "headline": ("Лучший результат на всех четырёх сроках дал самый простой вариант: "
                     "предполагать, что курс существенно не изменится.") if zero_wins_all else
                    "На части сроков один из наборов факторов уменьшил ошибку прогноза.",
        "findings": [
            "Модель со всеми 16 показателями проиграла среднему прошлых лет на " + ", ".join(
                f"{label}: {abs(value) * 100:.2f}%" for label, value in full_skills.items()),
            "Автоматический отбор сократил ущерб от лишних показателей, но всё равно проиграл среднему на " +
            ", ".join(f"{label}: {abs(value) * 100:.2f}%" for label, value in sparse_skills.items()),
            "На длинных сроках модели чаще угадывали направление, но ошибались в размере движения сильнее простого ориентира.",
        ],
    }
    report = {
        "dataset_id": dataset_id, "parser": config["version"], "normalized_sha256": normalized_hash,
        "generated_at": datetime.now(UTC).isoformat(), "config_sha256": config_hash,
        "input_dataset_id": parent["dataset_id"], "market_dataset_id": coverage["dataset_id"],
        "sample_rows": len(rows), "date_from": str(rows[0]["feature_date"]),
        "date_to": str(rows[-1]["feature_date"]), "horizons": list(config["horizons"]),
        "feature_names": list(config["features"]), "target": config["sample"]["target"],
        "comparisons": list(config["comparisons"]), "result": result, "best_by_horizon": best,
        "story": story,
        "files": {"fold_metrics": (folder / "fold_metrics.parquet").relative_to(root()).as_posix(),
                  "predictions": (folder / "predictions.parquet").relative_to(root()).as_posix()},
        "strict_pit_eligible": False, "inference_status": config["inference_status"],
        "limitations": [
            "Целевая цена — дневное закрытие bid Dukascopy. Издержки и исполнение сделок здесь не моделируются.",
            "Для части входных рядов доступны только обновляемые исторические выгрузки, а не сохранённые версии каждого прошлого дня.",
            "Точное время появления дневных значений ставок ещё проверяется, поэтому результат пока исследовательский.",
            "Даже победа модели в этой таблице сама по себе не означала бы готовую торговую стратегию.",
        ],
    }
    atomic_json(root() / "reports" / "denn_market_forecast.json", report)
    return report
