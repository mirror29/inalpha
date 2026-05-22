"""``POST /strategies/compose`` API 集成测试。"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _trend_payload() -> dict[str, Any]:
    return {
        "hint": {
            "family": "trend",
            "params": {"fast_period": 10, "slow_period": 30, "trade_size": 0.02},
            "reasoning": "momentum dominates",
        },
        "factors": [
            {
                "name": "sma20_cross_up",
                "kind": "momentum",
                "value": 1.02,
                "strength": 0.7,
                "horizon": "swing",
                "explanation": "20-bar SMA crossed 50",
            }
        ],
        "timeframe": "1h",
    }


def test_compose_requires_auth(client: TestClient) -> None:
    r = client.post("/strategies/compose", json=_trend_payload())
    assert r.status_code == 401


def test_compose_trend_returns_sma_cross(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post("/strategies/compose", json=_trend_payload(), headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy_id"] == "sma_cross"
    assert body["params"]["fast_period"] == 10
    assert body["params"]["slow_period"] == 30
    assert body["rejected_reason"] is None


def test_compose_none_family_rejects(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    payload = {
        "hint": {"family": "none", "params": {}, "reasoning": "ambiguous"},
        "factors": [],
    }
    r = client.post("/strategies/compose", json=payload, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["strategy_id"] is None
    assert body["rejected_reason"] is not None


def test_compose_mean_reversion_with_num_std_alias(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """LLM 用 num_std → API 也应该兼容。"""
    payload = {
        "hint": {
            "family": "mean_reversion",
            "params": {"period": 18, "num_std": 1.8},
            "reasoning": "RSI extreme",
        },
        "factors": [],
    }
    r = client.post("/strategies/compose", json=payload, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["strategy_id"] == "mean_reversion"
    assert body["params"]["std_mult"] == 1.8


def test_compose_invalid_payload_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """schema 校验：factor.strength 超界。inalpha_shared error handler 统一返 400。"""
    payload = {
        "hint": {"family": "trend", "params": {}, "reasoning": ""},
        "factors": [
            {"name": "f", "kind": "momentum", "value": 1.0, "strength": 5.0}
        ],
    }
    r = client.post("/strategies/compose", json=payload, headers=auth_headers)
    assert r.status_code in (400, 422)
