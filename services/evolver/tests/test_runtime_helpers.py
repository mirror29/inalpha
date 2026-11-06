from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from inalpha_evolver.api.cursor import decode_cursor, encode_cursor
from inalpha_evolver.runtime.executor import _parse_config


def test_cursor_round_trip() -> None:
    queued_at = datetime(2026, 8, 13, 5, 30, tzinfo=UTC)
    run_id = uuid4()
    assert decode_cursor(encode_cursor(queued_at, run_id)) == (queued_at, run_id)


def test_invalid_cursor_is_bad_request() -> None:
    with pytest.raises(HTTPException) as caught:
        decode_cursor("not-a-cursor")
    assert caught.value.status_code == 400


def test_parse_config_restores_datetime() -> None:
    parsed = _parse_config(
        {
            "from_ts": "2026-08-01T00:00:00Z",
            "as_of": "2026-08-13T00:00:00+00:00",
            "venue": "binance",
        }
    )
    assert parsed["from_ts"] == datetime(2026, 8, 1, tzinfo=UTC)
    assert parsed["as_of"] == datetime(2026, 8, 13, tzinfo=UTC)
    assert parsed["venue"] == "binance"


def test_run_response_retains_failure_cost_and_marks_unconfirmed_usage():
    from inalpha_evolver.api.presenters import run_response

    row = {
        "run_id": uuid4(), "seed_strategy_id": "seed", "budget": 4, "config": {},
        "status": "failed", "queued_at": datetime.now(UTC), "llm_cost_usd": 0,
    }
    response = run_response(row, summary={
        "recorded_cost_usd": 0.0123, "known_cost_usd": 0, "unknown_usage_count": 1,
    })
    assert response.llm_cost_usd == 0.0123
    assert response.known_cost_usd == 0
    assert response.unknown_usage_count == 1
