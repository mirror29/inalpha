"""Per-case budget diagnostics: matching target and safety cap are reported separately.

Known provider-reported usage is never mixed with unknown attempts: a case with any unknown
attempt keeps its known subtotal but is excluded from matching statistics and counted apart.
"""

from __future__ import annotations

import statistics
from collections import Counter
from fractions import Fraction
from typing import Any

# BudgetStop reasons raised by the runner. Token stops are budget boundaries; accounting
# stops come from unconfirmed ledger settlement or unknown usage and are kept distinct.
TOKEN_STOPS = frozenset({"TOKEN_BUDGET_OR_UNKNOWN_USAGE", "CAMPAIGN_BUDGET"})
ACCOUNTING_STOPS = frozenset({"RESERVATION_EXCEEDED", "LEDGER_UNCONFIRMED", "UNKNOWN_USAGE"})
MEDIAN_TOLERANCE = Fraction(1, 10)
CASE_TOLERANCE = Fraction(1, 4)
CASE_SHARE = 0.80


def run_usage(run: dict[str, Any]) -> dict[str, Any]:
    """Known tokens, unknown attempts and retries for one run, including failed attempts."""
    calls = run.get("calls", [])
    known = [c for c in calls if c.get("usage", {}).get("usage_status") == "known"]
    return {
        "case_id": run["case_id"],
        "status": run["status"],
        "reason": run.get("reason"),
        "attempts": len(calls),
        "retries": sum(1 for c in calls if c.get("attempt", 0) > 0),
        "failed_attempts": sum(1 for c in calls if c.get("error")),
        "unknown_attempts": len(calls) - len(known),
        "known_input_tokens": sum(c["usage"]["input_tokens"] for c in known),
        "known_output_tokens": sum(c["usage"]["output_tokens"] for c in known),
        "known_tokens": sum(
            c["usage"]["input_tokens"] + c["usage"]["output_tokens"] for c in known
        ),
        "charged_reservation_tokens": run.get("tokens_charged"),
        "completed_units": run.get("completed_units", 0),
    }


def spread(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "q1": None, "q3": None, "iqr": None, "min": None, "max": None}
    q1, _, q3 = (
        statistics.quantiles(values, n=4, method="inclusive")
        if len(values) > 1
        else (values[0],) * 3
    )
    return {
        "median": statistics.median(values),
        "q1": q1,
        "q3": q3,
        "iqr": q3 - q1,
        "min": min(values),
        "max": max(values),
    }


def within(tokens: float, target: int, tolerance: Fraction) -> bool:
    """Inclusive |tokens - target| <= tolerance * target in exact arithmetic, so a
    boundary such as 1,100 against 1,000 at ±10% is never lost to float rounding."""
    return abs(Fraction(tokens) - target) <= tolerance * target


def budget_diagnostics(
    runs: list[dict[str, Any]],
    *,
    caps: dict[str, int | None],
    targets: dict[str, int | None],
) -> dict[str, Any]:
    """Report usage against a declared matching target, separately from the safety cap.

    ``targets`` maps arm to the preregistered per-case matching target T (None when not
    declared: ratios and the matching verdict are then omitted, never inferred from data).
    ``caps`` maps arm to the runner's token cap, a guard that is not a matching device.
    """
    report: dict[str, Any] = {}
    for arm in sorted({r["arm"] for r in runs}):
        rows = [run_usage(r) for r in runs if r["arm"] == arm]
        clean = [row for row in rows if row["unknown_attempts"] == 0]
        tokens = [float(row["known_tokens"]) for row in clean]
        target = targets.get(arm)
        cap = caps.get(arm)
        cases = []
        for row in rows:
            ratio = (
                row["known_tokens"] / target if target and row["unknown_attempts"] == 0 else None
            )
            cases.append(
                {
                    **row,
                    "ratio_to_target": ratio,
                    "ratio_to_cap": row["known_tokens"] / cap if cap else None,
                }
            )
        median = statistics.median(tokens) if tokens else None
        known = [row["known_tokens"] for row in clean]
        within_case = (
            sum(within(t, target, CASE_TOLERANCE) for t in known) / len(known)
            if target and known
            else None
        )
        median_ok = (
            within(median, target, MEDIAN_TOLERANCE) if target and median is not None else None
        )
        reasons = Counter(row["reason"] for row in rows if row["reason"])
        report[arm] = {
            "cases": len(rows),
            "matching_target_tokens": target,
            "safety_cap_tokens": cap,
            "usage": spread(tokens),
            "cases_excluded_unknown_usage": len(rows) - len(clean),
            "unknown_attempts": sum(row["unknown_attempts"] for row in rows),
            "attempts": sum(row["attempts"] for row in rows),
            "retries": sum(row["retries"] for row in rows),
            "failed_attempts": sum(row["failed_attempts"] for row in rows),
            "median_ratio_to_target": median / target if target and median is not None else None,
            "share_cases_within_25_percent": within_case,
            "matching_achieved": None
            if median_ok is None or within_case is None
            else bool(median_ok and within_case >= CASE_SHARE),
            "token_budget_stops": sum(n for r, n in reasons.items() if r in TOKEN_STOPS),
            "accounting_stops": sum(n for r, n in reasons.items() if r in ACCOUNTING_STOPS),
            "stop_reasons": dict(sorted(reasons.items())),
            "invalid_runs": sum(row["status"] != "completed" for row in rows),
            "invalid_rate": sum(row["status"] != "completed" for row in rows) / len(rows),
            "completed_units": dict(
                sorted(Counter(str(row["completed_units"]) for row in rows).items())
            ),
            "per_case": cases,
        }
    return {
        "criterion": "median within ±10% of target and ≥80% of cases within ±25%",
        "known_usage_only": "cases with any unknown attempt are excluded from matching "
        "statistics and counted separately; unknown usage is never zero-filled",
        "arms": report,
    }
