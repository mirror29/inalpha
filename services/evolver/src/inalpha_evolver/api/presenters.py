"""Evolver API 响应组装。"""
from __future__ import annotations

from typing import Any

from .schemas import CandidateResponse, RunStatusResponse


def candidate_response(row: dict[str, Any]) -> CandidateResponse:
    return CandidateResponse.model_validate(row)


def run_response(
    row: dict[str, Any],
    *,
    candidate_rows: list[dict[str, Any]] | None = None,
    summary: dict[str, Any] | None = None,
) -> RunStatusResponse:
    data = dict(row)
    stats = summary or {}
    data.update(
        known_cost_usd=float(stats.get("known_cost_usd", 0)),
        unknown_usage_count=int(stats.get("unknown_usage_count", 0)),
        attempted=int(stats.get("attempted", 0)),
        succeeded=int(stats.get("succeeded", 0)),
        rejected=int(stats.get("rejected", 0)),
        candidates=[candidate_response(item) for item in candidate_rows or []],
    )
    data["llm_cost_usd"] = max(
        float(data.get("llm_cost_usd", 0)), float(stats.get("recorded_cost_usd", 0)),
    )
    return RunStatusResponse.model_validate(data)


__all__ = ["candidate_response", "run_response"]
