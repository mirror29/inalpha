"""Formal multi-ticker inference for #206 v0.3 §3.

Pooled Brier skill against each case's own ticker climatology, and the three primary
contrasts D - X (X in A, B, C; negative favours D) under a moving-block bootstrap over
decision dates: every ticker on a resampled date moves together. Two-sided p-values are
Holm-adjusted across the three contrasts.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

from .report import CLASSES, forecast_brier, metrics

CONTRASTS = (("D", "A"), ("D", "B"), ("D", "C"))
PRIMARY_BLOCK = 4
SENSITIVITY_BLOCKS = (2, 8)
RESAMPLES = 10_000
SEED = 206
ALPHA = 0.05


def case_scores(
    runs: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    climatology: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """Strict intention-to-treat Brier per arm plus the ticker-climatology reference.

    Every labelled case must have exactly one run per arm; a missing run is an error, never
    a silent drop, because dropping would break the pairing behind each contrast.
    """
    by_case: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for run in runs:
        if run["arm"] in by_case[run["case_id"]]:
            raise ValueError("duplicate run for case and arm")
        by_case[run["case_id"]][run["arm"]] = run
    scores = []
    for label in labels:
        arms = by_case.get(label["case_id"], {})
        if set(arms) != set("ABCD"):
            raise ValueError(f"case {label['case_id']} lacks a run for every arm")
        base = climatology[label["ticker"]]
        scores.append(
            {
                "case_id": label["case_id"],
                "ticker": label["ticker"],
                "date": label["decision_session"],
                "brier": {arm: metrics(arms[arm], label)["brier"] for arm in "ABCD"},
                "climatology_brier": forecast_brier(
                    {key: base[cls] for key, cls in CLASSES}, label
                ),
            }
        )
    return scores


def skill(rows: list[dict[str, Any]], arm: str) -> float | None:
    reference = sum(row["climatology_brier"] for row in rows)
    return 1 - sum(row["brier"][arm] for row in rows) / reference if reference else None


def holm(pvalues: dict[str, float], alpha: float = ALPHA) -> dict[str, dict[str, Any]]:
    """Holm step-down: adjusted p_(k) = max over j <= k of min(1, (m - j + 1) p_(j))."""
    ordered = sorted(pvalues, key=lambda key: pvalues[key])
    adjusted, running = {}, 0.0
    for rank, key in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * pvalues[key]))
        adjusted[key] = running
    return {
        key: {"p": pvalues[key], "p_holm": adjusted[key], "reject": adjusted[key] <= alpha}
        for key in pvalues
    }


def date_bootstrap(
    scores: list[dict[str, Any]],
    *,
    block: int = PRIMARY_BLOCK,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Moving-block bootstrap over sorted decision dates; a contrast is the pooled mean
    difference over all ticker-dates drawn. p-values test zero by centring the bootstrap
    distribution on the estimate: p = (1 + #|d* - d| >= |d|) / (1 + R)."""
    dates = sorted({row["date"] for row in scores})
    if len(dates) <= block:
        # At equality the only block is the full sequence: every resample reproduces the
        # estimate, which would report certainty from a single possible draw.
        return {"block_length": block, "reason": "TOO_FEW_DATES", "contrasts": None}
    names = [f"{x}-{y}" for x, y in CONTRASTS]
    sums: dict[str, defaultdict[str, float]] = {name: defaultdict(float) for name in names}
    counts: dict[str, int] = defaultdict(int)
    for row in scores:
        counts[row["date"]] += 1
        for (x, y), name in zip(CONTRASTS, names, strict=True):
            sums[name][row["date"]] += row["brier"][x] - row["brier"][y]
    estimate = {name: sum(sums[name].values()) / len(scores) for name in names}
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {name: [] for name in names}
    for _ in range(resamples):
        sample: list[str] = []
        while len(sample) < len(dates):
            start = rng.randrange(len(dates) - block + 1)
            sample.extend(dates[start : start + block])
        sample = sample[: len(dates)]
        n = sum(counts[d] for d in sample)
        for name in names:
            draws[name].append(sum(sums[name][d] for d in sample) / n)
    pvalues, intervals = {}, {}
    for name in names:
        values = sorted(draws[name])
        extreme = sum(abs(v - estimate[name]) >= abs(estimate[name]) for v in values)
        pvalues[name] = (1 + extreme) / (1 + resamples)
        intervals[name] = [
            values[int(0.025 * resamples)],
            values[min(resamples - 1, int(0.975 * resamples))],
        ]
    tests = holm(pvalues)
    return {
        "block_length": block,
        "resamples": resamples,
        "seed": seed,
        "dates": len(dates),
        "cases": len(scores),
        "contrasts": {
            name: {"difference": estimate[name], "interval_95": intervals[name], **tests[name]}
            for name in names
        },
    }


def formal_inference(
    runs: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    climatology: dict[str, dict[str, float]],
    *,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """Pooled and per-ticker skill plus primary and sensitivity bootstrap contrasts."""
    scores = case_scores(runs, labels, climatology)
    tickers = sorted({row["ticker"] for row in scores})
    return {
        "scoring": "strict intention-to-treat Brier; climatology per ticker",
        "pooled_bss": {arm: skill(scores, arm) for arm in "ABCD"},
        "per_ticker_bss": {
            ticker: {
                arm: skill([r for r in scores if r["ticker"] == ticker], arm) for arm in "ABCD"
            }
            for ticker in tickers
        },
        "primary": date_bootstrap(scores, block=PRIMARY_BLOCK, resamples=resamples, seed=seed),
        "sensitivity": [
            date_bootstrap(scores, block=b, resamples=resamples, seed=seed)
            for b in SENSITIVITY_BLOCKS
        ],
        "family": "Holm across D-A, D-B, D-C at alpha 0.05",
    }


def claims(primary: dict[str, Any], confirmatory: dict[str, Any]) -> dict[str, Any]:
    """A contrast is claimed only when the retrospective test rejects after Holm and the
    forward period estimates the same nonzero direction; zero in either period is no
    direction."""
    out = {}
    for name, test in (primary["primary"]["contrasts"] or {}).items():
        forward = (confirmatory["primary"]["contrasts"] or {}).get(name)
        sign = (test["difference"] > 0) - (test["difference"] < 0)
        same = (
            forward is not None
            and sign != 0
            and sign == (forward["difference"] > 0) - (forward["difference"] < 0)
        )
        out[name] = {
            "retrospective_reject": test["reject"],
            "same_direction_forward": same,
            "claim": bool(test["reject"] and same),
            "favours": None if sign == 0 else "D" if sign < 0 else name.split("-")[1],
        }
    return out
