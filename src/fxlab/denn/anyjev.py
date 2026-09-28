"""Deterministic, point-in-time-shaped contracts for the AnyJev shadow pilot."""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path

import yaml

CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "anyjev.yaml"


def load_anyjev_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "anyjev-fx-1":
        raise ValueError("Unsupported AnyJev config version")
    horizons = [int(item["horizon_sessions"]) for item in config["questions"]]
    if horizons != [1, 5, 20, 60] or len({item["id"] for item in config["questions"]}) != 4:
        raise ValueError("AnyJev questions must define unique 1/5/20/60-session heads")
    if [item["id"] for item in config["options"]] != ["down", "flat", "up"]:
        raise ValueError("AnyJev option order is frozen as down/flat/up")
    feature_ids = [item["id"] for item in config["features"]]
    if len(feature_ids) != len(set(feature_ids)) or not feature_ids:
        raise ValueError("AnyJev features must be non-empty and unique")
    if int(config["runtime"]["blocks"]) != 24:
        raise ValueError("The verified AnyJev checkpoint has 24 blocks")
    digest = hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()
    return config, digest


def return_bucket(value: float, lower: float, upper: float) -> int:
    """Map a future log return to down/flat/up using past-fold bounds."""
    if not all(math.isfinite(float(item)) for item in (value, lower, upper)) or lower >= upper:
        raise ValueError("Return and ordered bucket bounds must be finite")
    return 0 if value < lower else (2 if value > upper else 1)


def render_world_state(snapshot: dict, config: dict) -> str:
    """Render one stable prompt state without adding interpretation or future values."""
    prediction_time = snapshot.get("prediction_time")
    if not isinstance(prediction_time, datetime) or prediction_time.tzinfo is None:
        raise ValueError("prediction_time must be a timezone-aware datetime")
    available_at_max = snapshot.get("available_at_max")
    if available_at_max is not None:
        if not isinstance(available_at_max, datetime) or available_at_max.tzinfo is None:
            raise ValueError("available_at_max must be timezone-aware when present")
        if available_at_max > prediction_time:
            raise ValueError("Snapshot contains data that was not available at prediction_time")
    lines = [
        "FX Causal Lab world state",
        f"prediction_time: {prediction_time.isoformat()}",
        f"point_in_time_status: {snapshot.get('point_in_time_status', 'unknown')}",
    ]
    values = snapshot.get("features", {})
    for feature in config["features"]:
        value = values.get(feature["id"])
        if value is None:
            rendered = "missing"
        else:
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(f"Non-finite AnyJev feature: {feature['id']}")
            rendered = f"{number:+.8g}"
        lines.append(f"{feature['title']} [{feature['unit']}]: {rendered}")
    text = snapshot.get("published_text")
    if text is not None:
        clean = " ".join(str(text).split())
        lines.append(f"published_text: {clean}" if clean else "published_text: missing")
    return "\n".join(lines)


def build_anyjev_job(snapshot: dict, question_id: str, config: dict, config_sha256: str) -> dict:
    question = next((item for item in config["questions"] if item["id"] == question_id), None)
    if question is None:
        raise ValueError(f"Unknown AnyJev question: {question_id}")
    state = render_world_state(snapshot, config)
    state_sha256 = hashlib.sha256(state.encode()).hexdigest()
    payload = {
        "schema_version": "fxlab-anyjev-job-1",
        "mode": config["mode"],
        "question_id": question_id,
        "horizon_sessions": int(question["horizon_sessions"]),
        "options": [item["id"] for item in config["options"]],
        "prediction_time": snapshot["prediction_time"].isoformat(),
        "state": state,
        "state_sha256": state_sha256,
        "config_sha256": config_sha256,
        "strict_pit_eligible": bool(snapshot.get("strict_pit_eligible", False)),
    }
    payload["job_id"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]
    return payload


def llama_cpp_choice_probabilities(response: dict, token_to_option: dict[str, str]) -> dict[str, float]:
    """Normalize selected llama.cpp token log-probabilities for an AnyJev L0 comparison."""
    try:
        candidates = response["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("llama.cpp response has no top token probabilities") from error
    scores = {}
    for candidate in candidates:
        token = candidate.get("token")
        if token in token_to_option:
            scores[token_to_option[token]] = float(candidate["logprob"])
    missing = set(token_to_option.values()) - set(scores)
    if missing:
        raise ValueError(f"llama.cpp response is missing option probabilities: {sorted(missing)}")
    peak = max(scores.values())
    weights = {option: math.exp(score - peak) for option, score in scores.items()}
    total = sum(weights.values())
    return {option: weight / total for option, weight in weights.items()}
