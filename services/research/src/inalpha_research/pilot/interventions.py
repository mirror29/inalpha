"""Intervention case selection and accounting (#206 v0.3 §4).

Selection is conditional on a valid D base run: conclusions about intervention sensitivity
hold only for cases whose base discussion completed. Every count keeps its denominator.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

INTERVENTIONS = (
    "delete_contribution",
    "replace_contribution",
    "flip_conclusion",
    "downstream_noise",
    "remove_evidence",
    "remove_placebo_evidence",
    "round_zero_noise",
)
NOISE_FOR = {
    "delete_contribution": "downstream_noise",
    "replace_contribution": "downstream_noise",
    "flip_conclusion": "downstream_noise",
    "remove_evidence": "round_zero_noise",
    "remove_placebo_evidence": "round_zero_noise",
}
RULES = ("first_n", "seeded_random")


def selection_rule(configuration: dict[str, Any] | None, default_size: int = 1) -> dict[str, Any]:
    """Validate a selection configuration; campaigns call this before any provider call."""
    configuration = configuration or {}
    rule = configuration.get("rule", "first_n")
    size, seed = configuration.get("size", default_size), configuration.get("seed")
    if rule not in RULES:
        raise ValueError("unknown intervention selection rule")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("intervention size must be a non-negative integer")
    if rule == "seeded_random" and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError("seeded_random selection requires a preregistered integer seed")
    return {"rule": rule, "size": size, "seed": seed}


def select_cases(
    runs: list[dict[str, Any]],
    case_ids: list[str],
    *,
    rule: str = "first_n",
    size: int = 1,
    seed: int | None = None,
) -> dict[str, Any]:
    """Pick intervention cases before any replay; never top up from invalid base runs.

    ``first_n`` keeps the development behaviour (the first N cases in input order, valid or
    not). ``seeded_random`` draws min(size, valid pool) cases from cases whose D base run
    completed, using a preregistered seed; a shortfall is reported, not filled.
    """
    selection_rule({"rule": rule, "size": size, "seed": seed})
    status = {r["case_id"]: r["status"] for r in runs if r["arm"] == "D"}
    valid = [case for case in case_ids if status.get(case) == "completed"]
    if rule == "first_n":
        selected = case_ids[:size]
    else:
        chosen = set(random.Random(seed).sample(valid, min(size, len(valid))))
        selected = [case for case in case_ids if case in chosen]
    return {
        "rule": rule,
        "seed": seed,
        "size_requested": size,
        "full_pool": len(case_ids),
        "valid_d_pool": len(valid),
        "selected": selected,
        "shortfall": max(0, size - len(valid)) if rule == "seeded_random" else None,
        "conditional_on": "valid D base run" if rule == "seeded_random" else None,
    }


def accounting(
    replays: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    selection: dict[str, Any] | None,
) -> dict[str, Any]:
    """Per-intervention selected/skipped/attempted/failed/valid/valid-pair counts.

    Skip rate = skipped / selected; failure rate = failed / attempted. A valid
    intervention-noise pair needs a completed base, a completed intervention replay and a
    completed matching noise replay on the same case.
    """
    base = {r["case_id"]: r for r in runs if r["arm"] == "D"}
    if selection is None:
        seen = dict.fromkeys(r["case_id"] for r in replays)
        valid = sum(r["status"] == "completed" for r in base.values())
        selection = {
            "rule": "inferred from replays (no selection record)",
            "full_pool": len(base),
            "valid_d_pool": valid,
            "selected": list(seen),
        }
    selected = selection["selected"]
    outcome: dict[tuple[str, str], tuple[str, str | None]] = {}
    for row in replays:
        case = row["case_id"]
        if "skip" in row:
            names = INTERVENTIONS if row.get("name") == "interventions" else (row["name"],)
            for name in names:
                outcome[case, name] = ("skipped", row["skip"])
            continue
        valid_run = row["status"] == "completed" and base[case]["status"] == "completed"
        outcome[case, row["replay"]["name"]] = ("valid" if valid_run else "failed", None)
    for case in selected:
        removal = outcome.get((case, "remove_evidence"), ("skipped", "NOT_PLANNED"))
        for name in INTERVENTIONS:
            if (case, name) not in outcome:
                inherited = (
                    removal[1] if name in ("remove_placebo_evidence", "round_zero_noise") else None
                )
                outcome[case, name] = ("skipped", f"NOT_PLANNED:{inherited or 'UNKNOWN'}")
    table = {}
    for name in INTERVENTIONS:
        states = [outcome[case, name] for case in selected]
        skipped = [reason for state, reason in states if state == "skipped"]
        attempted = sum(state != "skipped" for state, _ in states)
        failed = sum(state == "failed" for state, _ in states)
        pairs = (
            sum(
                outcome[case, name][0] == "valid" and outcome[case, NOISE_FOR[name]][0] == "valid"
                for case in selected
            )
            if name in NOISE_FOR
            else None
        )
        table[name] = {
            "selected": len(selected),
            "skipped": len(skipped),
            "skip_reasons": dict(sorted(Counter(skipped).items())),
            "attempted": attempted,
            "failed_or_invalid": failed,
            "valid": attempted - failed,
            "valid_intervention_noise_pairs": pairs,
            "noise_control": NOISE_FOR.get(name),
            "skip_rate": len(skipped) / len(selected) if selected else None,
            "failure_rate": failed / attempted if attempted else None,
        }
    return {
        "selection": {k: v for k, v in selection.items() if k != "selected"},
        "selected_cases": len(selected),
        "denominators": "skip_rate = skipped / selected; failure_rate = failed_or_invalid / "
        "attempted; sensitivity metrics use valid intervention-noise pairs only",
        "interventions": table,
    }
