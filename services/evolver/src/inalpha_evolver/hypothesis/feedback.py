"""Freeze compact E1 simulation evidence for event-campaign proposal prompts."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from typing import Any

from ..data.persistent_snapshot import discovery_dataset

SOURCE_SIMULATION_FEEDBACK_VERSION = "source-simulation-feedback-v2"

_DISCOVERY_METRICS = (
    "sharpe",
    "total_return_pct",
    "max_drawdown_pct",
    "num_trades",
    "num_bars",
)


def build_source_simulation_feedback(
    run: dict[str, Any],
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a bounded, source-free snapshot for the first event generation."""
    return _build_feedback(run, candidate_rows, discovery_only=False)


def build_loop_simulation_feedback(
    run: dict[str, Any], candidate_rows: list[dict[str, Any]], frozen_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Use full discovery metrics only after proving the baseline never saw later bars."""
    expected = discovery_dataset(frozen_snapshot).manifest.model_dump(mode="json")
    if (
        str(run["owner_account_id"]) != str(frozen_snapshot["owner_account_id"])
        or run.get("dataset_manifest") != expected
    ):
        raise ValueError("baseline does not match the immutable discovery dataset")
    return _build_feedback(run, candidate_rows, discovery_only=True)


def _build_feedback(
    run: dict[str, Any], candidate_rows: list[dict[str, Any]], *, discovery_only: bool,
) -> dict[str, Any]:
    seed = _report_metrics(run.get("seed_report_snapshot"), discovery_only=discovery_only)
    baseline = _report_metrics(run.get("baseline_snapshot"), discovery_only=discovery_only)
    if not seed or not baseline:
        raise ValueError("completed source run is missing seed or baseline feedback")

    outcome_counts = Counter(str(row.get("outcome") or "unknown") for row in candidate_rows)
    error_counts = Counter(
        str(row["error_code"])[:80] for row in candidate_rows if row.get("error_code")
    )
    successful = []
    for row in candidate_rows:
        metrics = _report_metrics(row.get("evaluation_snapshot"), discovery_only=discovery_only)
        score = _finite_number(metrics.get("sharpe"))
        if row.get("outcome") == "succeeded" and score is not None:
            successful.append((row, metrics, score))
    successful.sort(key=lambda item: (-float(item[2]), int(item[0].get("slot") or 0)))
    scores = [float(item[2]) for item in successful]
    seed_score = _finite_number(seed.get("sharpe"))
    baseline_score = _finite_number(baseline.get("sharpe"))
    summary = {
        "attempted": len(candidate_rows),
        "succeeded": outcome_counts.get("succeeded", 0),
        "rejected": len(candidate_rows) - outcome_counts.get("succeeded", 0),
        "outcomes": dict(sorted(outcome_counts.items())),
        "errors": dict(sorted(error_counts.items())),
        "metric_scope": "loop_discovery_only" if discovery_only else "source_train_only",
        "seed_discovery_score": seed_score,
        "baseline_discovery_score": baseline_score,
        "best_candidate_discovery_score": scores[0] if scores else None,
        "median_candidate_discovery_score": statistics.median(scores) if scores else None,
    }
    records: list[dict[str, Any]] = [
        {"scope": "source_run_summary", **summary},
        {
            "scope": "source_seed",
            "strategy_id": str(run.get("seed_strategy_id") or "")[:128],
            "metrics": seed,
        },
        {"scope": "buy_and_hold_baseline", "metrics": baseline},
    ]
    for row, metrics, score in successful[:4]:
        records.append(
            {
                "scope": "source_candidate",
                "slot": int(row.get("slot") or 0),
                "discovery_score": score,
                "score_delta_vs_seed": (
                    score - seed_score if seed_score is not None else None
                ),
                "score_delta_vs_baseline": (
                    score - baseline_score if baseline_score is not None else None
                ),
                "overfitting_risk": str(row.get("overfitting_risk") or "unknown")[:20],
                "metrics": metrics,
            }
        )

    digest_payload = {
        "version": SOURCE_SIMULATION_FEEDBACK_VERSION,
        "source_run_id": str(run["run_id"]),
        "summary": summary,
        "records": records,
    }
    feedback_sha256 = hashlib.sha256(
        json.dumps(
            digest_payload,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {**digest_payload, "feedback_sha256": feedback_sha256}


def _report_metrics(snapshot: object, *, discovery_only: bool = False) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    validation = snapshot.get("validation")
    train = validation.get("train") if isinstance(validation, dict) else None
    if discovery_only:
        train = snapshot
    if not isinstance(train, dict):
        return {}
    return {
        key: number
        for key in _DISCOVERY_METRICS
        if (number := _finite_number(train.get(key))) is not None
    }


def _finite_number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(float(value)) else None


__all__ = [
    "SOURCE_SIMULATION_FEEDBACK_VERSION",
    "build_loop_simulation_feedback",
    "build_source_simulation_feedback",
]
