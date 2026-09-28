#!/usr/bin/env python3
"""Fit an AnyJev L2 head with chronological validation and score a later test period."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import yaml


def compact_state(state: str, maximum: int) -> str:
    if len(state) <= maximum:
        return state
    marker = "\npublished_text: "
    prefix, separator, text = state.partition(marker)
    if not separator:
        return state[:maximum]
    allowance = max(1000, maximum - len(prefix) - len(marker) - 80)
    first = int(allowance * 0.75)
    last = allowance - first
    return prefix + marker + text[:first] + " [middle omitted deterministically] " + text[-last:]


def softmax(scores: np.ndarray, temperature: float) -> np.ndarray:
    shifted = scores / temperature
    shifted -= shifted.max(axis=1, keepdims=True)
    weights = np.exp(shifted)
    return weights / weights.sum(axis=1, keepdims=True)


def log_loss(probabilities: np.ndarray, labels: np.ndarray) -> float:
    selected = probabilities[np.arange(len(labels)), labels]
    return float(-np.mean(np.log(np.clip(selected, 1e-12, None))))


def brier(probabilities: np.ndarray, labels: np.ndarray) -> float:
    truth = np.zeros_like(probabilities)
    truth[np.arange(len(labels)), labels] = 1.0
    return float(np.mean(np.sum((probabilities - truth) ** 2, axis=1)))


def split_rows(rows: list[dict], spec: dict) -> dict[str, list[dict]]:
    result = {}
    for name in ("train", "validation", "test"):
        start = str(spec[name]["from"])
        end = str(spec[name]["to"])
        result[name] = [row for row in rows if start <= row["feature_date"] <= end]
        if not result[name]:
            raise ValueError(f"Empty chronological split: {name}")
    seen = [row["job_id"] for values in result.values() for row in values]
    if len(seen) != len(set(seen)):
        raise ValueError("Chronological splits overlap")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--head-output", type=Path)
    args = parser.parse_args()
    config_bytes = args.config.read_bytes()
    config = yaml.safe_load(config_bytes)
    if config.get("version") != "anyjev-l2-chrono-1":
        raise ValueError("Unsupported chronological L2 config")
    if config["mixing"] != {"numeric_prediction_in_state": False, "comparison_only_after_freeze": True}:
        raise ValueError("AnyJev and numeric predictions must remain independent")
    rows = sorted((json.loads(line) for line in args.bundle.read_text(encoding="utf-8").splitlines() if line.strip()),
                  key=lambda row: (row["feature_date"], row["event_id"]))
    if any(row["horizon_sessions"] != config["horizon_sessions"] for row in rows):
        raise ValueError("Bundle contains a different forecast horizon")
    forbidden = ("actual_return", "actual_direction", "forecast_return", "quantitative_direction")
    if any(any(token in row["state"] for token in forbidden) for row in rows):
        raise ValueError("A future outcome or numeric prediction leaked into an AnyJev state")
    splits = split_rows(rows, config["splits"])
    ordered = [row for name in ("train", "validation", "test") for row in splits[name]]
    states = [compact_state(row["state"], int(config["max_state_chars"])) for row in ordered]
    labels = np.asarray([int(row["label_index"]) for row in ordered], dtype=int)

    from anyjev import Decider, Question
    from anyjev.backends.hf import HFBackend
    from anyjev.heads import LinearHead, solve_ridge

    started = time.monotonic()
    backend = HFBackend(config["model_path"], device="cuda", dtype="bfloat16", batch_size=1,
                        local_files_only=True)
    question = Question.choice(
        "What will EUR/USD do over the next 5 trading days?",
        ["fall", "stay broadly flat", "rise"], name="fx_return_5d_l2_chrono_v1")
    decider = Decider(backend, level="L2", adapt=False)
    features = decider._features(question, states, [int(config["layer"])])[:, 0, :]
    counts = {name: len(values) for name, values in splits.items()}
    n_train = counts["train"]
    n_validation = counts["validation"]
    train_slice = slice(0, n_train)
    validation_slice = slice(n_train, n_train + n_validation)
    test_slice = slice(n_train + n_validation, len(ordered))

    train_x, train_y = features[train_slice], labels[train_slice]
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0) + 1e-6
    train_z = (train_x - mean) / scale
    validation_z = (features[validation_slice] - mean) / scale
    candidates = []
    for ridge_lambda in (float(value) for value in config["ridge_lambdas"]):
        W, b = solve_ridge(train_z, train_y, 3, lam=ridge_lambda)
        scores = validation_z @ W + b
        for temperature in (float(value) for value in config["temperatures"]):
            probabilities = softmax(scores, temperature)
            candidates.append((log_loss(probabilities, labels[validation_slice]), -ridge_lambda,
                               ridge_lambda, temperature))
    _, _, selected_lambda, selected_temperature = min(candidates)

    fit_x = features[:n_train + n_validation]
    fit_y = labels[:n_train + n_validation]
    fit_mean = fit_x.mean(axis=0)
    fit_scale = fit_x.std(axis=0) + 1e-6
    W, b = solve_ridge((fit_x - fit_mean) / fit_scale, fit_y, 3, lam=selected_lambda)
    head = LinearHead(kind="ridge", layer=0, W=W, b=b, mean=fit_mean, scale=fit_scale,
                      temperature=selected_temperature, params={"lam": selected_lambda,
                      "listing": "canonical", "selection": "chronological_validation"},
                      n_calib=len(fit_y), cv={})
    test_probabilities = head.probs(features[test_slice])
    test_labels = labels[test_slice]
    option_ids = list(config["options"])
    test_rows = ordered[n_train + n_validation:]
    predictions = []
    for row, probability, label in zip(test_rows, test_probabilities, test_labels):
        predicted = int(np.argmax(probability))
        predictions.append({
            "job_id": row["job_id"], "event_id": row["event_id"],
            "prediction_time": row["prediction_time"], "feature_date": row["feature_date"],
            "horizon_sessions": int(row["horizon_sessions"]), "track": "anyjev",
            "model_id": config["model"], "level": "L2", "split": "test",
            "direction": option_ids[predicted],
            "probabilities": {option_ids[i]: float(probability[i]) for i in range(3)},
            "actual_not_in_request": True, "state_sha256": row["state_sha256"],
            "state_chars_total": int(row["state_chars_total"]),
            "state_chars_used": len(compact_state(row["state"], int(config["max_state_chars"]))),
            "elapsed_seconds": 0.0,
        })
    direction_counts = {name: sum(row["direction"] == name for row in predictions) for name in option_ids}
    accuracy = float(np.mean(np.argmax(test_probabilities, axis=1) == test_labels))
    majority_accuracy = max(np.bincount(test_labels, minlength=3)) / len(test_labels)
    result = {
        "schema_version": "fxlab-anyjev-l2-result-1", "status": "complete",
        "bundle_sha256": hashlib.sha256(args.bundle.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(config_bytes.replace(b"\r\n", b"\n")).hexdigest(),
        "model_id": config["model"], "model_path": config["model_path"],
        "anyjev_commit": config["anyjev_commit"], "level": "L2", "layer": int(config["layer"]),
        "split_rows": counts, "selected_lambda": selected_lambda,
        "selected_temperature": selected_temperature, "test_accuracy": accuracy,
        "test_log_loss": log_loss(test_probabilities, test_labels),
        "test_brier": brier(test_probabilities, test_labels),
        "test_majority_class_accuracy": float(majority_accuracy),
        "direction_counts": direction_counts, "predictions": predictions,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "numeric_prediction_in_state": False, "selection_scheme": "past_train_then_validation_then_later_test",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.head_output:
        args.head_output.parent.mkdir(parents=True, exist_ok=True)
        args.head_output.write_text(json.dumps(head.to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "predictions"}, indent=2))


if __name__ == "__main__":
    main()
