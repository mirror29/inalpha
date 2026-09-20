"""Evolver API schema 与 presenter 单测。"""

import copy
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from inalpha_evolver.api.presenters import candidate_response, run_response
from inalpha_evolver.api.request_hash import approval_request_digest, normalized_request
from inalpha_evolver.api.schemas import (
    CampaignResponse,
    EvolutionConfig,
    EvolutionLLMSnapshot,
    RunStatusResponse,
    StartRunRequest,
    compute_llm_config_digest,
)

from .llm_snapshot_fixtures import VALID_LLM_SNAPSHOT, llm_snapshot


def _request(symbol: str = "BTCUSDT") -> StartRunRequest:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    return StartRunRequest(
        config=EvolutionConfig(
            venue="binance",
            symbol=symbol,
            timeframe="1h",
            from_ts=now - timedelta(days=30),
            as_of=now,
        ),
        llm=EvolutionLLMSnapshot.model_validate(llm_snapshot()),
    )


def test_request_hash_is_stable_and_payload_sensitive() -> None:
    config_a, hash_a = normalized_request(_request())
    config_b, hash_b = normalized_request(_request())
    _config_c, hash_c = normalized_request(_request("ETHUSDT"))

    assert config_a == config_b
    assert hash_a == hash_b
    assert hash_a != hash_c


def test_approval_request_digest_matches_typescript_contract() -> None:
    assert (
        approval_request_digest(_request())
        == "f0fd300e586f96bf3f9cba26b4b3452660fbc51c0bab0274ad196bb0f53ff64f"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("model",), "tampered-model"),
        (("pricing", "input_usd_per_million"), 0.01),
    ],
)
def test_llm_snapshot_digest_rejects_tampering(
    path: tuple[str, ...],
    value: str | float,
) -> None:
    payload = copy.deepcopy(VALID_LLM_SNAPSHOT)
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(ValueError, match="pricing is unavailable"):
        EvolutionLLMSnapshot.model_validate(payload)


def test_deepseek_official_v1_alias_is_canonicalized() -> None:
    payload = llm_snapshot()
    payload["base_url"] = "https://api.deepseek.com/v1"

    snapshot = EvolutionLLMSnapshot.model_validate(payload)

    assert snapshot.base_url == "https://api.deepseek.com"


def test_llm_snapshot_digest_matches_typescript_contract() -> None:
    snapshot = EvolutionLLMSnapshot.model_validate(llm_snapshot())
    assert snapshot.config_digest == VALID_LLM_SNAPSHOT["config_digest"]


def test_non_openai_compatible_provider_is_rejected() -> None:
    payload = llm_snapshot()
    payload["provider"] = "anthropic"
    with pytest.raises(ValueError):
        EvolutionLLMSnapshot.model_validate(payload)


def test_custom_or_private_llm_endpoint_is_rejected_even_with_matching_digest() -> None:
    snapshot = EvolutionLLMSnapshot.model_validate(llm_snapshot()).model_copy(
        update={"base_url": "http://169.254.169.254/latest/meta-data"}
    )
    payload = snapshot.model_dump()
    payload["config_digest"] = compute_llm_config_digest(snapshot)

    with pytest.raises(ValueError, match="official deepseek API endpoint"):
        EvolutionLLMSnapshot.model_validate(payload)


def test_invalid_window_is_rejected() -> None:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    with pytest.raises(ValueError):
        EvolutionConfig(
            venue="binance",
            symbol="BTCUSDT",
            timeframe="1h",
            from_ts=now,
            as_of=now,
        )


def test_run_presenter_converts_numeric_cost() -> None:
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    response = run_response(
        {
            "run_id": uuid4(),
            "seed_strategy_id": "sma_cross_v1",
            "budget": 4,
            "config": {},
            "status": "queued",
            "llm_cost_usd": "0.1250",
            "queued_at": now,
        },
        summary={"attempted": 2, "succeeded": 1, "rejected": 1},
    )
    assert response.llm_cost_usd == pytest.approx(0.125)
    assert response.attempted == 2


def test_historical_campaign_snapshot_does_not_depend_on_current_pricing_catalog() -> None:
    """已冻结的旧模型快照必须可读，但不能绕过新请求的价格准入。"""
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    historical_snapshot = copy.deepcopy(VALID_LLM_SNAPSHOT)
    historical_snapshot["model"] = "deepseek-v4-pro"
    historical_snapshot["pricing"]["version"] = "provider-estimate-2026-08"

    response = CampaignResponse.model_validate(
        {
            "campaign_id": uuid4(),
            "owner_account_id": uuid4(),
            "status": "failed",
            "active_generation": 5,
            "hypothesis_budget": 8,
            "implementations_per_hypothesis": 3,
            "max_generations": 5,
            "event_snapshot_id": uuid4(),
            "frozen_config": {},
            "llm_snapshot": historical_snapshot,
            "llm_config_digest": historical_snapshot["config_digest"],
            "llm_cost_usd": 0,
            "state_version": 1,
            "created_at": now,
            "updated_at": now,
        }
    )

    assert response.llm_snapshot.model == "deepseek-v4-pro"
    with pytest.raises(ValueError, match="pricing is unavailable"):
        EvolutionLLMSnapshot.model_validate(historical_snapshot)


def test_datetime_inputs_normalize_or_fail_without_type_error() -> None:
    now = datetime.now(UTC) - timedelta(minutes=1)
    config = EvolutionConfig(
        venue="binance",
        symbol="BTCUSDT",
        timeframe="1h",
        from_ts=(now - timedelta(days=1)).replace(tzinfo=None),
        as_of=now.astimezone(timezone(timedelta(hours=9))),
    )
    assert config.from_ts.tzinfo == UTC
    assert config.as_of.tzinfo == UTC
    with pytest.raises(ValueError, match="timezone-aware"):
        EvolutionConfig(
            venue="binance",
            symbol="BTCUSDT",
            timeframe="1h",
            from_ts=now - timedelta(days=1),
            as_of=now.replace(tzinfo=None),
        )


def test_future_as_of_returns_http_422() -> None:
    app = FastAPI()

    @app.post("/validate")
    async def validate(body: StartRunRequest) -> dict[str, bool]:
        return {"ok": bool(body)}

    future = datetime.now(UTC) + timedelta(minutes=1)
    response = TestClient(app).post(
        "/validate",
        json={
            "config": {
                "venue": "binance",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "from_ts": (future - timedelta(days=1)).isoformat(),
                "as_of": future.isoformat(),
            }
        },
    )
    assert response.status_code == 422
    assert "trusted current time" in response.text


def test_candidate_response_exposes_data_epoch() -> None:
    candidate_id, run_id = uuid4(), uuid4()
    response = candidate_response(
        {
            "candidate_id": candidate_id,
            "run_id": run_id,
            "slot": 1,
            "generation": 1,
            "stage": "evaluation",
            "outcome": "succeeded",
            "data_epoch": 1_786_000_000_000,
        }
    )
    assert response.data_epoch == 1_786_000_000_000


def test_run_dto_exposes_manifest_cutoff_and_lag() -> None:
    manifest = RunStatusResponse.model_json_schema()["$defs"]["DatasetManifest"]
    required = set(manifest["required"])
    assert {
        "latest_bar_ts",
        "cutoff_bar_ts",
        "freshness_lag_seconds",
        "data_epoch",
        "backfill",
    } <= required
