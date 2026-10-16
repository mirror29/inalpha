"""Deterministic paired development reporting and intervention construction."""

from __future__ import annotations

import copy
import math
import random
import statistics
from typing import Any

from .artifacts import safe_json

CLASSES = (("p_up", "UP"), ("p_flat", "FLAT"), ("p_down", "DOWN"))
PROBS = tuple(key for key, _ in CLASSES)
# Topic-unrelated natural prose for the length-matched replacement control. Digit-free so the
# numeric re-entry scan is unaffected; versioned because changing it changes the control.
NEUTRAL_CORPUS_VERSION = "neutral-prose-v1"
NEUTRAL_CORPUS = (
    "The community garden opened its gates early on Saturday. Volunteers sorted seed packets "
    "by colour, repaired two wooden benches and agreed to plant beans along the north fence, "
    "where the afternoon shade is lighter.",
    "Old stone bridges were often built with a slight arch so that rainwater would drain "
    "toward the banks. Many still carry foot traffic today, although their original toll "
    "houses have long since become cafes or small museums.",
    "A good loaf of bread needs patience more than skill. The dough rests, rises and is folded "
    "several times before baking, and the crust forms best when the oven holds steady heat "
    "for the first twenty minutes.",
    "Migrating birds use the position of the sun, the night sky and even the magnetic field "
    "of the earth to keep their course. Some species travel thousands of kilometres each "
    "year and return to the same nesting site.",
)


def neutral_text(length: int) -> str:
    """Deterministic unrelated prose of about ``length`` characters, cut at a word boundary."""
    text = " ".join(NEUTRAL_CORPUS)
    while len(text) < length:
        text = f"{text} {text}"
    cut = text[:length]
    return cut.rsplit(" ", 1)[0] if " " in cut and len(text) > length else cut


def intervention_plans(run: dict[str, Any], case: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep earlier turns, modify the selected last-round contribution, replay downstream."""
    if run["status"] != "completed":
        return [{"name": "interventions", "skip": "INVALID_BASE_RUN"}]
    history = run["history"]
    candidates = [(i, row) for i, row in enumerate(history) if "output" in row]
    if not candidates:
        return [{"name": "interventions", "skip": "NO_COMPLETED_TURN"}]
    last = max(row["round"] for _, row in candidates)
    probs = ("p_up", "p_flat", "p_down")
    selected, row = min(
        ((i, row) for i, row in candidates if row["round"] == last),
        key=lambda item: (
            sum(abs(item[1]["output"][p] - run["decision"][p]) for p in probs),
            item[0],
        ),
    )
    prefix = copy.deepcopy(history[: selected + 1])
    # Deletion keeps the step slot for replay alignment, but the turn is omitted from every
    # model-visible history, so later turns see no marker that something was removed.
    deleted = copy.deepcopy(prefix)
    deleted[-1] = {"role": row["role"], "round": row["round"], "omitted": True}
    # Evidence fixed, conclusion flipped: the original rationale stays verbatim and no meta
    # text is added. The rationale/conclusion inconsistency is part of the stimulus.
    flipped = copy.deepcopy(prefix)
    decision = flipped[-1]["output"]
    decision["p_up"], decision["p_down"] = decision["p_down"], decision["p_up"]
    decision["action"] = -decision["action"]
    placebo = copy.deepcopy(prefix)
    length = len(safe_json(row["output"]))
    placebo[-1] = {"role": row["role"], "round": row["round"], "message": neutral_text(length)}
    plans = [
        {"name": "delete_contribution", "prefix": deleted},
        {
            "name": "replace_contribution",
            "prefix": placebo,
            "matching": "approximate character length, not exact provider tokens",
            "corpus": NEUTRAL_CORPUS_VERSION,
        },
        {"name": "flip_conclusion", "prefix": flipped}
        if row["output"]["action"] != 0
        else {"name": "flip_conclusion", "skip": "NEUTRAL_CONCLUSION_HAS_NO_DIRECTIONAL_REVERSE"},
        {"name": "downstream_noise", "prefix": prefix, "stochastic_replicate": True},
    ]
    cited = run["decision"]["cited_evidence_ids"]
    if not cited:
        plans.append({"name": "remove_evidence", "skip": "NO_CITED_EVIDENCE"})
    else:
        removed = cited[0]
        items = {e["id"]: e for e in case["evidence"]}
        plans.append({"name": "remove_evidence", "remove_evidence": removed})
        uncited = [key for key in items if key not in cited]
        if uncited:
            target = min(
                uncited,
                key=lambda key: (
                    abs(
                        len(safe_json(items[key]["content"]))
                        - len(safe_json(items[removed]["content"]))
                    ),
                    key,
                ),
            )
            plans.append({"name": "remove_placebo_evidence", "remove_evidence": target})
        else:
            plans.append({"name": "remove_placebo_evidence", "skip": "NO_UNCITED_EVIDENCE"})
        plans.append({"name": "round_zero_noise", "stochastic_replicate": True})
    return plans


def forecast_brier(probs: dict[str, float], label: dict[str, Any]) -> float:
    return float(sum((probs[key] - (label["value"] == cls)) ** 2 for key, cls in CLASSES))


INVALID_POLICIES = ("strict", "uniform", "climatology")


def metrics(
    run: dict[str, Any],
    label: dict[str, Any],
    invalid_policy: str = "strict",
    climatology: dict[str, float] | None = None,
) -> dict[str, float]:
    """Invalid runs stay in intention-to-treat metrics as action 0.

    ``strict`` scores an invalid probability forecast at the maximum Brier (2.0), so failure
    can never beat a real forecast. ``uniform`` and ``climatology`` are sensitivity fallbacks.
    """
    if invalid_policy not in INVALID_POLICIES:
        raise ValueError("unknown invalid-run policy")
    invalid = run["status"] != "completed"
    if not invalid:
        value = run["decision"]
        brier = forecast_brier(value, label)
    elif invalid_policy == "strict":
        value, brier = {"action": 0}, 2.0
    else:
        if invalid_policy == "uniform":
            probs = dict.fromkeys(PROBS, 1 / 3)
        elif climatology is None:
            raise ValueError("climatology fallback requires base rates")
        else:
            probs = {key: climatology[cls] for key, cls in CLASSES}
        value, brier = {**probs, "action": 0}, forecast_brier(probs, label)
    action = int(value["action"])
    category = {1: "UP", 0: "FLAT", -1: "DOWN"}[action]
    pnl = action * label["excess_return"]
    return {
        "brier": brier,
        "hit": float(category == label["value"]),
        "coverage": float(action != 0),
        "invalid": float(invalid),
        "pnl_20bp": pnl - 0.002 * abs(action),
        "pnl_0bp": pnl,
        "pnl_50bp": pnl - 0.005 * abs(action),
    }


def sample_mean_run(run: dict[str, Any]) -> dict[str, Any]:
    """B-mean: average the completed B sample probabilities; same calls, no aggregation call.

    The action is the unique argmax class, or neutral when the maximum is tied.
    """
    outputs = [row["output"] for row in run["history"] if "output" in row]
    if not outputs:
        return {"status": "invalid", "decision": None}
    probs = {key: statistics.mean(o[key] for o in outputs) for key in PROBS}
    top = max(probs.values())
    winners = [
        action for action, key in zip((1, 0, -1), PROBS, strict=True) if top - probs[key] <= 1e-9
    ]
    return {
        "status": "completed",
        "decision": {**probs, "action": winners[0] if len(winners) == 1 else 0},
    }


def sample_disagreement(run: dict[str, Any]) -> float | None:
    """Share of completed B sample pairs whose actions differ (diversity diagnostic)."""
    actions = [row["output"]["action"] for row in run["history"] if "output" in row]
    pairs = [(a, b) for i, a in enumerate(actions) for b in actions[i + 1 :]]
    return statistics.mean(float(a != b) for a, b in pairs) if pairs else None


def paired_interval(values: list[float], block: int = 5) -> dict[str, Any]:
    """Seeded moving-block bootstrap; tiny development intervals are descriptive only."""
    if len(values) < block:
        return {"difference": statistics.mean(values), "interval": None, "reason": "TOO_FEW_CASES"}
    rng = random.Random(206)
    means = []
    for _ in range(1000):
        sample: list[float] = []
        while len(sample) < len(values):
            start = rng.randrange(len(values) - block + 1)
            sample.extend(values[start : start + block])
        means.append(statistics.mean(sample[: len(values)]))
    means.sort()
    return {
        "difference": statistics.mean(values),
        "interval": [means[24], means[974]],
        "block_length": block,
    }


def build_report(
    runs: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    replays: list[dict[str, Any]],
    *,
    climatology: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Require paired four-arm coverage; never silently drop missing or invalid cases.

    ``climatology`` holds UP/FLAT/DOWN base rates from the tau calibration windows; without
    it, climatology references, skill scores and the climatology ITT fallback are omitted.
    """
    if climatology is not None and (
        set(climatology) != {"UP", "FLAT", "DOWN"}
        or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1
            for v in climatology.values()
        )
        or abs(sum(climatology.values()) - 1) > 1e-6
    ):
        raise ValueError("climatology must be UP/FLAT/DOWN probabilities summing to one")
    if not labels:
        raise ValueError("nonempty outcome labels required")
    for label in labels:
        excess = label.get("excess_return")
        if (
            not isinstance(label.get("case_id"), str)
            or not label["case_id"]
            or label.get("value") not in ("UP", "FLAT", "DOWN")
            or isinstance(excess, bool)
            or not isinstance(excess, (int, float))
            or not math.isfinite(excess)
        ):
            raise ValueError("invalid outcome label")
    by_case = {label["case_id"]: label for label in labels}
    if len(by_case) != len(labels):
        raise ValueError("unique outcome case IDs required")
    lookup = {(r["case_id"], r["arm"]): r for r in runs}
    if len(lookup) != len(runs) or set(lookup) != {
        (case, arm) for case in by_case for arm in "ABCD"
    }:
        raise ValueError("exactly one run per case and arm required")
    scored = {
        (case, arm): metrics(lookup[case, arm], label)
        for case, label in by_case.items()
        for arm in "ABCD"
    }
    arms = {
        arm: {
            key: statistics.mean(scored[case, arm][key] for case in by_case)
            for key in next(iter(scored.values()))
        }
        for arm in "ABCD"
    }
    variant_runs = {(case, "B-mean"): sample_mean_run(lookup[case, "B"]) for case in by_case}
    for case, label in by_case.items():
        scored[case, "B-mean"] = metrics(variant_runs[case, "B-mean"], label)
    variants = {
        "B-mean": {
            key: statistics.mean(scored[case, "B-mean"][key] for case in by_case)
            for key in next(iter(scored.values()))
        }
    }
    references = {
        "uniform": statistics.mean(
            forecast_brier(dict.fromkeys(PROBS, 1 / 3), label) for label in by_case.values()
        ),
        "climatology": statistics.mean(
            forecast_brier({key: climatology[cls] for key, cls in CLASSES}, label)
            for label in by_case.values()
        )
        if climatology is not None
        else None,
    }
    all_arms = {**arms, **variants}
    skill = {
        arm: 1 - values["brier"] / references["climatology"] if references["climatology"] else None
        for arm, values in all_arms.items()
    }
    reliability = {
        arm: {
            cls: {
                "mean_predicted": statistics.mean(r["decision"][key] for r, _ in completed),
                "observed": statistics.mean(float(lab["value"] == cls) for _, lab in completed),
            }
            for key, cls in CLASSES
        }
        if (
            completed := [
                (r, by_case[case])
                for case in by_case
                if (r := variant_runs.get((case, arm)) or lookup.get((case, arm)))
                and r["status"] == "completed"
            ]
        )
        else {}
        for arm in (*"ABCD", "B-mean")
    }
    itt_sensitivity = {
        policy: {
            arm: statistics.mean(
                metrics(lookup[case, arm], label, policy, climatology)["brier"]
                for case, label in by_case.items()
            )
            for arm in "ABCD"
        }
        for policy in INVALID_POLICIES
        if policy != "strict" and (policy != "climatology" or climatology is not None)
    }
    disagreements = [
        d for case in by_case if (d := sample_disagreement(lookup[case, "B"])) is not None
    ]
    complete = [
        case
        for case in by_case
        if all(lookup[case, arm]["status"] == "completed" for arm in "ABCD")
    ]
    paired = {
        f"{a}-{b}": paired_interval(
            [scored[case, a]["brier"] - scored[case, b]["brier"] for case in by_case]
        )
        for a, b in (
            ("B", "A"),
            ("C", "A"),
            ("D", "A"),
            ("D", "B"),
            ("D", "C"),
            ("B-mean", "B"),
        )
    }
    intervention_rows = []
    for replay in replays:
        if "skip" in replay:
            intervention_rows.append(replay)
            continue
        base = lookup[replay["case_id"], "D"]
        valid = replay["status"] == base["status"] == "completed"
        intervention_rows.append(
            {
                "case_id": replay["case_id"],
                "name": replay["replay"]["name"],
                "status": replay["status"],
                "flip": float(replay["decision"]["action"] != base["decision"]["action"])
                if valid
                else None,
                "brier_change": metrics(replay, by_case[replay["case_id"]])["brier"]
                - metrics(base, by_case[replay["case_id"]])["brier"],
                "original_class_probability_change": (
                    replay["decision"][
                        max(("p_up", "p_flat", "p_down"), key=lambda k: base["decision"][k])
                    ]
                    - max(base["decision"][k] for k in ("p_up", "p_flat", "p_down"))
                )
                if valid
                else None,
                "reentry": replay.get("reentry"),
            }
        )
    noise = {(row["case_id"], row["name"]): row for row in intervention_rows if "flip" in row}
    for row in intervention_rows:
        if "flip" not in row:
            continue
        if row["name"] in ("downstream_noise", "round_zero_noise"):
            row["noise_adjusted_flip"] = None
            continue
        baseline_name = (
            "round_zero_noise"
            if row["name"] in ("remove_evidence", "remove_placebo_evidence")
            else "downstream_noise"
        )
        baseline = noise.get((row["case_id"], baseline_name), {})
        row["noise_adjusted_flip"] = (
            row["flip"] - baseline["flip"]
            if row["flip"] is not None and baseline.get("flip") is not None
            else None
        )
    budgets = {
        arm: {
            "median_actual_known_tokens": statistics.median(
                [
                    sum(
                        c["usage"]["input_tokens"] + c["usage"]["output_tokens"]
                        for c in r["calls"]
                        if c["usage"].get("usage_status") == "known"
                    )
                    for r in runs
                    if r["arm"] == arm
                ]
            ),
            "unknown_attempts": sum(
                c["usage"].get("usage_status") != "known"
                for r in runs
                if r["arm"] == arm
                for c in r["calls"]
            ),
        }
        for arm in "ABCD"
    }
    medians = [budgets[arm]["median_actual_known_tokens"] for arm in "BCD"]
    reference = statistics.mean(medians)
    budget_match = (
        reference > 0
        and all(abs(m / reference - 1) <= 0.1 for m in medians)
        and not any(budgets[arm]["unknown_attempts"] for arm in "BCD")
    )
    return {
        "compute_match_within_10_percent": budget_match,
        "scope": "development machinery validation; no superiority or causal attribution",
        "arms": arms,
        "invalid_run_policy": "strict: invalid forecast scored at Brier 2.0, action 0",
        "itt_sensitivity_brier": itt_sensitivity,
        "variants": variants,
        "references_brier": references,
        "brier_skill_vs_climatology": skill,
        "reliability": reliability,
        "b_sample_action_disagreement": statistics.mean(disagreements) if disagreements else None,
        "paired_brier": paired,
        "complete_case_count": len(complete),
        "complete_case_arms": {
            arm: {
                key: statistics.mean(scored[c, arm][key] for c in complete)
                for key in next(iter(scored.values()))
            }
            for arm in "ABCD"
        }
        if complete
        else {},
        "budgets": budgets,
        "interventions": intervention_rows,
        "limitations": [
            "budget matching requires development calibration before formal freeze",
            "model revision and training cutoff unknown",
            "historical vendor revisions cannot be excluded",
            "human process review pending",
            "intervention replacement matches characters, not exact provider tokens",
            "B-mean reuses B samples; it is a scoring variant, not an independently budgeted arm",
            "noise uses a fresh stochastic call; provider seed support is not assumed",
        ],
    }
