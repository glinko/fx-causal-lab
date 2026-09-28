#!/usr/bin/env python3
"""Run a bounded, resumable L0 pilot against the existing llama.cpp endpoint."""

import argparse
import json
import math
import time
import urllib.request
from pathlib import Path


def selected(jobs, limit):
    if not limit or limit >= len(jobs):
        return jobs
    if limit == 1:
        return [jobs[0]]
    indices = sorted({round(index * (len(jobs) - 1) / (limit - 1)) for index in range(limit)})
    return [jobs[index] for index in indices]


def compact_state(state, maximum):
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


def probabilities(payload):
    candidates = payload["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    mapping = {" A": "down", " B": "flat", " C": "up"}
    scores = {mapping[item["token"]]: float(item["logprob"]) for item in candidates if item.get("token") in mapping}
    if set(scores) != {"down", "flat", "up"}:
        raise ValueError(f"Missing option log probabilities: {scores}")
    peak = max(scores.values())
    weights = {name: math.exp(value - peak) for name, value in scores.items()}
    total = sum(weights.values())
    return {name: value / total for name, value in weights.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jobs", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--endpoint", default="http://127.0.0.1:18081/v1/completions")
    parser.add_argument("--limit", type=int, default=32)
    parser.add_argument("--max-state-chars", type=int, default=3500)
    args = parser.parse_args()
    jobs = selected([json.loads(line) for line in args.jobs.read_text(encoding="utf-8").splitlines()], args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    completed = {}
    if args.output.exists():
        completed = {item["job_id"]: item for item in map(json.loads, args.output.read_text(encoding="utf-8").splitlines())}
    with args.output.open("a", encoding="utf-8") as target:
        for index, job in enumerate(jobs, 1):
            if job["job_id"] in completed:
                continue
            state = compact_state(job["state"], args.max_state_chars)
            bounds = job["label_bounds"]
            prompt = (state + "\n\nQuestion: predict the EUR/USD log return over the next " +
                      str(job["horizon_sessions"]) + " trading sessions. Choose exactly one option. " +
                      f"A = below {bounds['down_below']:+.6f}; B = between {bounds['down_below']:+.6f} " +
                      f"and {bounds['up_above']:+.6f}; C = above {bounds['up_above']:+.6f}. Answer:")
            request_body = json.dumps({"prompt": prompt, "max_tokens": 1, "temperature": 0, "logprobs": 20,
                                       "grammar": 'root ::= " A" | " B" | " C"'}).encode()
            started = time.monotonic()
            request = urllib.request.Request(args.endpoint, data=request_body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = json.loads(response.read())
            probs = probabilities(raw)
            record = {"job_id": job["job_id"], "event_id": job["event_id"],
                      "prediction_time": job["prediction_time"], "horizon_sessions": job["horizon_sessions"],
                      "track": "anyjev", "model_id": job["model_id"], "probabilities": probs,
                      "direction": max(probs, key=probs.get), "state_sha256": job["state_sha256"],
                      "state_chars_total": len(job["state"]), "state_chars_used": len(state),
                      "actual_not_in_request": True, "elapsed_seconds": round(time.monotonic() - started, 3)}
            target.write(json.dumps(record, ensure_ascii=False) + "\n"); target.flush()
            print(f"{index}/{len(jobs)} {job['job_id']} {record['direction']} {record['elapsed_seconds']}s", flush=True)


if __name__ == "__main__":
    main()
