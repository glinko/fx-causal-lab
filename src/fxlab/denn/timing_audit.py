"""DENN timing / cutoff audit (v0.17, pre-registered protocol ``denn-timing-audit-1``).

Re-derives the v0.16 lag statistic under the world-snapshot publication model
frozen in ``config/timing_audit.yaml``.

Model
-----
* a factor value dated grid date ``t`` is usable for a decision on grid date
  ``d`` iff its effective publication — the LATEST publication among the nodes
  the value is derived from (DST-aware IANA zones, ``calendar_offset_days`` for
  EIA dailies) — is strictly before the decision cutoff on date ``d``;
* at each decision date the audit uses the LATEST usable factor value and
  pairs it with the target return starting from the decision date, at every
  registered lag (config ``registered_lags``);
* the v0.16 grid statistic is reproduced as the ``naive`` arm: factor value
  dated ``d`` paired with the target at the registered lag.

Anchor / consistency: under the most permissive cutoff (``end_of_day_21``) the
latest usable factor for the US-EA spreads is the same-day value, so the
corrected statistic must equal the naive statistic exactly (checked per
candidate). For the EIA factors the next-day publication offset is a fact, not
a timing choice, so ``end_of_day_21`` legitimately uses a one-day-older value.

Verdicts (config ``classification``): ``robust`` (Bonferroni-significant at the
strict cutoff, sign-stable in both chronological halves, placebo not
significant), ``timing_artifact`` (strong naive, fails the strict cutoff,
Bonferroni-significant at an evening cutoff), else ``not_reproducible``.

Pure Python (stdlib + duckdb + yaml) — the runtime image has no numpy/scipy.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ..store import atomic_json, root
from .pipeline import _write_parquet
from .spectral import _load_inputs, _transform, correlation

UTC = timezone.utc
NY = "America/New_York"
CONFIG_PATH = Path(os.environ.get("FXLAB_PROJECT", ".")) / "config" / "timing_audit.yaml"

# factor -> (raw grid column, transform method, asinh scale)  [== config/spectral.yaml]
FACTOR_SOURCES = {
    "SPREAD_2Y_CHANGE": ("US_EA_2Y", "difference", None),
    "SPREAD_10Y_CHANGE": ("US_EA_10Y", "difference", None),
    "BRENT_CHANGE": ("BRENT", "asinh_difference", 20.0),
    "WTI_CHANGE": ("WTI", "asinh_difference", 20.0),
    "VIX_RETURN": ("VIX", "log_difference", None),
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    body = path.read_bytes()
    config = yaml.safe_load(body)
    if config.get("version") != "denn-timing-audit-1":
        raise ValueError("Unsupported timing-audit config version")
    required = {
        "candidates", "timing_model", "candidate_nodes", "registered_lags",
        "cutoffs", "bootstrap", "classification",
    }
    if not required <= set(config):
        raise ValueError(f"timing-audit config missing keys: {sorted(required - set(config))}")
    if len(config["registered_lags"]) < 2:
        raise ValueError("At least two registered lags are required")
    if not config["cutoffs"]:
        raise ValueError("At least one cutoff is required")
    return config, hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


def _hhmm(value: str) -> tuple[int, int]:
    hours, minutes = str(value).split(":")
    return int(hours), int(minutes)


# ---------------------------------------------------------------------------
# World-snapshot availability
# ---------------------------------------------------------------------------


def _effective_publication(
    grid_date: date, node: str, timing: dict, ea_override: str | None = None
) -> datetime:
    """Aware-UTC instant at which a node value with grid date becomes usable."""
    entry = timing[node]
    if ea_override is not None and node in ("EA_2Y", "EA_10Y"):
        published, tz = ea_override, NY
    else:
        published, tz = entry["published"], entry["tz"]
    day = grid_date + timedelta(days=int(entry.get("calendar_offset_days", 0)))
    hh, mm = _hhmm(published)
    return datetime.combine(day, time(hh, mm), tzinfo=ZoneInfo(tz)).astimezone(UTC)


def _cutoff_instant(decision_date: date, ny_time: str) -> datetime:
    hh, mm = _hhmm(ny_time)
    return datetime.combine(decision_date, time(hh, mm), tzinfo=ZoneInfo(NY)).astimezone(UTC)


def _eff_level_series(
    dates: list[date], nodes: list[str], timing: dict,
    ea_override: str | None = None,
) -> list[datetime]:
    """eff_level[t] = latest node publication instant for grid date t (non-decreasing)."""
    out: list[datetime] = []
    for day in dates:
        latest: datetime | None = None
        for node in nodes:
            instant = _effective_publication(day, node, timing, ea_override)
            if latest is None or instant > latest:
                latest = instant
        out.append(latest)  # type: ignore[arg-type]
    return out


def _latest_usable(
    dates: list[date], eff_level: list[datetime], ny_time: str
) -> list[int]:
    """For decision date d: largest t <= d with eff_level[t] < cutoff(d), else -1.

    Both eff_level and the cutoff instants are non-decreasing, so one forward
    pointer per decision date gives O(n) total.
    """
    cuts = [_cutoff_instant(day, ny_time) for day in dates]
    pointer = -1
    result: list[int] = []
    for d, cut in enumerate(cuts):
        while pointer + 1 <= d and eff_level[pointer + 1] < cut:
            pointer += 1
        result.append(pointer)
    return result


# ---------------------------------------------------------------------------
# Inference (registered protocol)
# ---------------------------------------------------------------------------


def _sign_stable(pairs: list[tuple[float, float]]) -> bool:
    if len(pairs) < 20:
        return False
    half = len(pairs) // 2
    r1 = correlation([p[0] for p in pairs[:half]], [p[1] for p in pairs[:half]])
    r2 = correlation([p[0] for p in pairs[half:]], [p[1] for p in pairs[half:]])
    return r1 is not None and r2 is not None and r1 * r2 > 0


def _bootstrap_p(pairs: list[tuple[float, float]], observed_r: float | None,
                 replicates: int, seed: int) -> float | None:
    """Pair-residual bootstrap p-value for H0: slope = 0 (two-sided on |r|).

    OLS (intercept + slope) is fitted once to estimate the residual scale;
    each replicate draws y* = ȳ + resampled residual under the NULL model
    (slope 0) and recomputes r*.  p = P(|r*| >= |r_obs|).  Resampling
    residuals around the fitted alternative would centre the distribution at
    the observed statistic (p ~= 0.5 always) and test nothing.
    """
    n = len(pairs)
    if n < 30 or observed_r is None:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    xm = sum(xs) / n
    ym = sum(ys) / n
    xc = [x - xm for x in xs]
    ssx = sum(c * c for c in xc)
    if ssx <= 1e-18:
        return None
    slope = sum(c * (y - ym) for c, y in zip(xc, ys)) / ssx
    intercept = ym - slope * xm
    resid = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    abs_obs = abs(observed_r)
    rng = random.Random(seed)
    randrange = rng.randrange
    count = 0
    for _ in range(replicates):
        ysum = 0.0
        sy2 = 0.0
        scy = 0.0
        for i in range(n):
            y = ym + resid[randrange(n)]
            ysum += y
            sy2 += y * y
            scy += xc[i] * y
        ym2 = ysum / n
        syy = sy2 - ysum * ym2
        if syy <= 1e-18:
            continue
        # sum((x-xm)(y-ym2)) == sum(xc_i * y_i) because sum(xc_i) == 0
        if abs(scy / math.sqrt(ssx * syy)) >= abs_obs:
            count += 1
    return (count + 1) / (replicates + 1)


def _evaluate(
    pairs_by_lag: dict[int, list[tuple[float, float]]],
    registered_lags: list[int],
    alpha: float,
    replicates: int,
    seed: int,
    do_bootstrap: bool = True,
) -> tuple[list[dict], dict | None]:
    """Point corr for every registered lag; bootstrap p-value ONLY for the lag
    with maximum |r| (ties -> smaller lag), vs Bonferroni threshold alpha/k."""
    threshold = alpha / len(registered_lags)
    per_lag: list[dict] = []
    best: dict | None = None
    best_pairs: list[tuple[float, float]] = []
    for lag in registered_lags:
        pairs = pairs_by_lag.get(lag, [])
        r = correlation([p[0] for p in pairs], [p[1] for p in pairs])
        entry = {
            "lag": lag, "n": len(pairs), "corr": r,
            "p_bootstrap": None,
            "sign_stable_halves": _sign_stable(pairs),
            "passes_bonferroni": None,
        }
        per_lag.append(entry)
        if r is not None and (best is None or abs(r) > abs(best["corr"])
                              or (abs(r) == abs(best["corr"]) and lag < best["lag"])):
            best = entry
            best_pairs = pairs
    if best is not None and do_bootstrap:
        pval = _bootstrap_p(best_pairs, best["corr"], replicates, seed + best["lag"])
        best["p_bootstrap"] = pval
        best["passes_bonferroni"] = pval is not None and pval < threshold
    return per_lag, best


def _placebo(
    factor: list[float], target: list[float], lag: int, shift: int,
    replicates: int, seed: int, threshold: float,
) -> dict | None:
    """Correlation of a circularly shifted factor at ``lag`` (red flag only)."""
    if lag < 0 or len(factor) <= shift + lag + 2:
        return None
    shifted = factor[shift:]
    left, right = shifted[:-lag], target[lag:]
    m = min(len(left), len(right))
    pairs = list(zip(left[:m], right[:m]))
    r = correlation([p[0] for p in pairs], [p[1] for p in pairs])
    pval = _bootstrap_p(pairs, r, replicates, seed + shift)
    return {
        "shift_sessions": shift, "lag": lag, "n": len(pairs), "corr": r,
        "p_bootstrap": pval,
        "passes_bonferroni": pval is not None and pval < threshold,
    }


# ---------------------------------------------------------------------------
# Pair builders
# ---------------------------------------------------------------------------
# Grid: dates[0..n-1], level v[0..n-1].
#   target T[j] = logdiff v[j+1]/v[j]        (j = 0..n-2)
#   factor F[j] = level[j+1] - level[j] etc. (j = 0..n-2, "dated" grid date j+1)
# Decision date d (index in dates, d >= 1):
#   naive     : (F[d-1], T[d-1+lag])
#   corrected : (F[latest[d]-1], T[d-1+lag]), latest[d] = largest t <= d
#               with eff_level[t] < cutoff(d)


def _naive_pairs(factor: list[float], target: list[float],
                 registered_lags: list[int]) -> dict[int, list[tuple[float, float]]]:
    out: dict[int, list[tuple[float, float]]] = {}
    for lag in registered_lags:
        if lag >= 0:
            left, right = factor[: len(factor) - lag], target[lag:]
        else:
            left, right = factor[-lag:], target[: len(factor) + lag]
        m = min(len(left), len(right))
        out[lag] = list(zip(left[:m], right[:m]))
    return out


def _corrected_pairs(
    factor: list[float], latest: list[int], target: list[float],
    registered_lags: list[int],
) -> dict[int, list[tuple[float, float]]]:
    n = len(factor) + 1  # grid size
    out: dict[int, list[tuple[float, float]]] = {}
    for lag in registered_lags:
        left: list[float] = []
        right: list[float] = []
        for d in range(1, n - (lag if lag > 0 else 0)):
            t_index = d - 1 + lag
            if t_index < 0 or t_index > len(target) - 1:
                continue
            t = latest[d]
            if t < 1:
                continue
            left.append(factor[t - 1])
            right.append(target[t_index])
        out[lag] = list(zip(left, right))
    return out


def _exploratory_scan(
    factor: list[float], latest: list[int], target: list[float],
    lags: list[int],
) -> dict[int, float | None]:
    """Point corr over an exploratory lag range at the strict cutoff. Reported,
    never used for the verdict."""
    out: dict[int, float | None] = {}
    n = len(factor) + 1
    for lag in lags:
        left: list[float] = []
        right: list[float] = []
        for d in range(1, n):
            t_index = d - 1 + lag
            if t_index < 0 or t_index > len(target) - 1:
                continue
            t = latest[d]
            if t < 1:
                continue
            left.append(factor[t - 1])
            right.append(target[t_index])
        out[lag] = correlation(left, right)
    return out


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def _passes(settings: dict, cutoff_names: list[str]) -> bool:
    for name in cutoff_names:
        ev = settings.get(name)
        if ev and ev.get("best") and ev["best"].get("passes_bonferroni") is True:
            return True
    return False


def _classify(
    naive: dict, corrected: dict, classification: dict
) -> tuple[str, bool]:
    strict_name = classification["strict_cutoff"]
    strict = corrected.get(strict_name, {})
    strict_best = strict.get("best") or {}
    strict_pass = strict_best.get("passes_bonferroni") is True
    naive_best = naive.get("best") or {}
    placebo_rows = (naive.get("placebo") or {}).values()
    placebo_clean = all(
        row.get("passes_bonferroni") is not True for row in placebo_rows
    )
    confirmed = (
        strict_pass
        and strict_best.get("sign_stable_halves") is True
        and placebo_clean
    )
    if confirmed:
        return "robust", True
    if not strict_pass and _passes(
        corrected, list(classification.get("evening_cutoffs", []))
    ) and naive_best.get("passes_bonferroni") is True:
        return "timing_artifact", False
    return "not_reproducible", False


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def build_timing_audit(config_path: Path = CONFIG_PATH) -> dict:
    config, config_hash = _load_config(config_path)
    dates, values, coverage, _baseline = _load_inputs()
    timing = config["timing_model"]
    registered_lags = list(config["registered_lags"])
    alpha = float(config["bootstrap"]["alpha"])
    replicates = int(config["bootstrap"]["replicates"])
    seed = int(config["bootstrap"]["seed"])
    placebo_shifts = list(config.get("placebo", {}).get("shifts_sessions", []))
    classification = config["classification"]
    candidate_nodes = config["candidate_nodes"]
    strict_name = classification["strict_cutoff"]

    target = _transform(values["EURUSD_REF"], "log_difference")

    candidates_out: dict[str, dict] = {}
    lag_table_rows: list[dict] = []

    for name in config["candidates"]:
        column, method, scale = FACTOR_SOURCES[name]
        factor = _transform(values[column], method, scale)
        nodes = candidate_nodes[name]
        threshold = alpha / len(registered_lags)

        # --- naive arm (v0.16 grid statistic) ---
        naive_pairs = _naive_pairs(factor, target, registered_lags)
        naive_per_lag, naive_best = _evaluate(
            naive_pairs, registered_lags, alpha, replicates, seed
        )
        naive: dict = {
            "per_lag": naive_per_lag,
            "best": naive_best,
            "placebo": {},
        }
        if naive_best is not None:
            for shift in placebo_shifts:
                row = _placebo(
                    factor, target, naive_best["lag"], shift,
                    replicates, seed, threshold,
                )
                if row is not None:
                    naive["placebo"][f"shift_{shift}"] = row

        # --- availability-corrected arm, every registered cutoff ---
        corrected: dict[str, dict] = {}
        eff_base = _eff_level_series(dates, nodes, timing, None)
        cutoffs_by_name = {c["name"]: c for c in config["cutoffs"]}
        for cutoff in config["cutoffs"]:
            latest = _latest_usable(dates, eff_base, cutoff["ny_time"])
            pairs = _corrected_pairs(factor, latest, target, registered_lags)
            per_lag, best = _evaluate(
                pairs, registered_lags, alpha, replicates, seed
            )
            corrected[cutoff["name"]] = {"per_lag": per_lag, "best": best}
            for row in per_lag:
                lag_table_rows.append(
                    {"candidate": name, "setting": f"corrected/{cutoff['name']}", **row}
                )

        # --- exploratory lag scan at the strict cutoff (reported only) ---
        latest_strict = _latest_usable(dates, eff_base, cutoffs_by_name[strict_name]["ny_time"])
        raw_exploratory = list(config.get("exploratory_lags", []))
        if len(raw_exploratory) == 2:
            exploratory_lags = list(range(raw_exploratory[0], raw_exploratory[1] + 1))
        else:
            exploratory_lags = raw_exploratory
        exploratory = _exploratory_scan(
            factor, latest_strict, target, exploratory_lags
        )

        # --- EA publication sensitivity (factors with EA nodes only; point
        #     correlations, base seed; the main verdict uses the base time) ---
        ea_sensitivity: dict[str, dict] = {}
        ea_times = list(config.get("ea_sensitivity_ny_times", []))
        ea_cutoff_names = list(config.get("ea_sensitivity_cutoffs", []))
        if any(node in ("EA_2Y", "EA_10Y") for node in nodes) and ea_times:
            for ea_time in ea_times:
                eff = _eff_level_series(dates, nodes, timing, ea_override=ea_time)
                for cutoff_name in ea_cutoff_names:
                    cutoff = cutoffs_by_name[cutoff_name]
                    latest = _latest_usable(dates, eff, cutoff["ny_time"])
                    pairs = _corrected_pairs(factor, latest, target, registered_lags)
                    per_lag, best = _evaluate(
                        pairs, registered_lags, alpha, replicates, seed,
                        do_bootstrap=False,
                    )
                    ea_sensitivity[f"{ea_time}/{cutoff_name}"] = {
                        "per_lag": per_lag, "best": best,
                    }

        verdict, confirmed = _classify(naive, corrected, classification)
        candidates_out[name] = {
            "naive": naive,
            "corrected": corrected,
            "exploratory_strict_07": {str(k): v for k, v in exploratory.items()},
            "ea_sensitivity": ea_sensitivity,
            "verdict": verdict,
            "confirmed": confirmed,
        }
        for row in naive_per_lag:
            lag_table_rows.append({"candidate": name, "setting": "naive", **row})

    # --- consistency: end_of_day_21 must equal naive for same-day factors ---
    consistency: dict[str, dict] = {}
    for name, entry in candidates_out.items():
        naive_lag1 = next(
            (r["corr"] for r in entry["naive"]["per_lag"] if r["lag"] == 1), None
        )
        eod = entry["corrected"].get("end_of_day_21", {}).get("per_lag", [])
        eod_lag1 = next((r["corr"] for r in eod if r["lag"] == 1), None)
        has_offset = any(
            int(timing[node].get("calendar_offset_days", 0)) > 0
            for node in candidate_nodes[name]
        )
        consistency[name] = {
            "naive_lag1_corr": naive_lag1,
            "end_of_day_lag1_corr": eod_lag1,
            "match": (
                naive_lag1 is not None and eod_lag1 is not None
                and abs(naive_lag1 - eod_lag1) < 1e-9
            ),
            "note": (
                "EIA next-day publication: end_of_day uses a one-day-older factor by design"
                if has_offset
                else "same-day factors: end_of_day must reproduce naive exactly"
            ),
        }

    deterministic = {
        "parser": config["version"],
        "config_sha256": config_hash,
        "input_dataset_id": coverage["dataset_id"],
        "input_sha256": coverage["normalized_sha256"],
        "date_from": str(dates[1]),
        "date_to": str(dates[-1]),
        "target": "EURUSD_REF",
        "registered_lags": registered_lags,
        "bootstrap": dict(config["bootstrap"]),
        "candidates": {
            name: {
                "naive": entry["naive"],
                "corrected": entry["corrected"],
                "exploratory_strict_07": entry["exploratory_strict_07"],
                "ea_sensitivity": entry["ea_sensitivity"],
                "verdict": entry["verdict"],
                "confirmed": entry["confirmed"],
            }
            for name, entry in candidates_out.items()
        },
        "consistency": consistency,
    }
    normalized_hash = hashlib.sha256(
        json.dumps(
            deterministic, sort_keys=True, default=str,
            separators=(",", ":"), allow_nan=False,
        ).encode()
    ).hexdigest()
    dataset_id = normalized_hash[:20]
    folder = root() / "gold" / "denn_timing_audit" / dataset_id
    folder.mkdir(parents=True, exist_ok=True)
    _write_parquet(lag_table_rows, folder / "lag_table.parquet", "candidate,setting,lag")

    report = {
        **deterministic,
        "report": "denn_timing_audit",
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset_id,
        "normalized_sha256": normalized_hash,
        "files": {
            "lag_table": (folder / "lag_table.parquet").relative_to(root()).as_posix()
        },
        "strict_pit_eligible": False,
        "inference_status": "multiplicity_controlled_published_at_model_without_vintages",
        "limitations": [
            "Inputs are current-history downloads; the audit models publication times (documented assumptions in timing_audit.yaml) rather than observed vintages (ALFRED/FRED).",
            "A confirmed/robust verdict is a predictive association under a timing model, never a causal claim.",
            "The EA AAA curve publication time is an assumption; see ea_sensitivity.",
            "Bootstrap p-values are computed for the best registered lag only, per the pre-registered protocol.",
        ],
    }
    atomic_json(root() / "reports" / "denn_timing_audit.json", report)
    return report


def run(config_path: Path = CONFIG_PATH) -> dict:
    return build_timing_audit(config_path)
