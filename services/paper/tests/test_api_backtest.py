"""``POST /backtest`` API 集成测试 —— 用 respx mock data-service。"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from .conftest import make_bar_row

pytestmark = pytest.mark.integration


def _bars_for_oscillating(n: int = 100) -> list[dict[str, Any]]:
    """合成 sin 波价格的 bars JSON list（data-service 风格）。"""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    bars = []
    for i in range(n):
        close = 100 + 10 * math.sin(2 * math.pi * i / 20)
        ts = base + timedelta(hours=i)
        bars.append(make_bar_row(ts.isoformat(), close=close))
    return bars


# ─── auth ───


def test_backtest_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/backtest",
        json={
            "strategy_id": "sma_cross",
            "symbol": "BTC/USDT",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-05T00:00:00Z",
        },
    )
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


# ─── strategy registry ───


def test_get_strategies(client: TestClient, auth_headers: dict[str, str]) -> None:
    r = client.get("/strategies", headers=auth_headers)
    assert r.status_code == 200
    assert "sma_cross" in r.json()["strategies"]


def test_unknown_strategy_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/backtest",
        headers=auth_headers,
        json={
            "strategy_id": "no-such-strategy",
            "symbol": "BTC/USDT",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-05T00:00:00Z",
        },
    )
    assert r.status_code == 400
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_inverted_time_range_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/backtest",
        headers=auth_headers,
        json={
            "strategy_id": "sma_cross",
            "symbol": "BTC/USDT",
            "from_ts": "2026-01-05T00:00:00Z",
            "to_ts": "2026-01-01T00:00:00Z",  # 反了
        },
    )
    assert r.status_code == 400


# ─── 端到端（mocked data-service） ───


@respx.mock
def test_backtest_e2e_with_mocked_data(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """模拟 data-service 返回 100 根 sin 波 bar，paper-service 跑 SMA cross 返回报告。"""
    bars = _bars_for_oscillating(100)

    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=bars)
    )

    r = client.post(
        "/backtest",
        headers=auth_headers,
        json={
            "strategy_id": "sma_cross",
            "params": {
                "fast_period": 5,
                "slow_period": 15,
                "trade_size": 0.05,
            },
            "venue": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-05T00:00:00Z",
            "initial_cash": 10_000.0,
            "fee_rate": 0.001,
        },
    )

    assert r.status_code == 200, r.json()
    body = r.json()

    assert body["strategy_id"] == "sma_cross"
    assert body["symbol"] == "BTC/USDT"
    assert body["venue"] == "binance"
    assert body["initial_cash"] == 10_000.0
    assert body["num_bars_processed"] == 100
    # 振荡价格 SMA cross 必然触发交易
    assert body["num_trades"] >= 2
    # equity 接近初始值（振荡市 + 手续费稍亏，不会爆赚爆亏）
    assert 9_000 <= body["final_equity"] <= 11_000
    # 绩效字段（D-7+ 新加）应该全部出现在响应里
    assert "sharpe" in body
    assert "sortino" in body
    assert "max_drawdown_pct" in body
    assert "win_rate" in body
    assert "equity_curve" in body
    assert len(body["equity_curve"]) == body["num_bars_processed"]
    # 每个 equity 点结构正确
    p0 = body["equity_curve"][0]
    assert "ts" in p0 and "equity" in p0
    # 振荡市必然有回撤
    assert body["max_drawdown_pct"] > 0.0


@respx.mock
def test_backtest_data_service_unavailable(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """data-service 不可达，应返回 502 + DATA_SERVICE_UNREACHABLE。"""
    import httpx

    respx.get("http://data-mock.test/bars").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    r = client.post(
        "/backtest",
        headers=auth_headers,
        json={
            "strategy_id": "sma_cross",
            "symbol": "BTC/USDT",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-05T00:00:00Z",
        },
    )
    assert r.status_code == 502
    assert r.json()["code"] == "DATA_SERVICE_UNREACHABLE"


@respx.mock
def test_backtest_empty_bars_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """data-service 返 0 根 bar → 400 NO_BARS_AVAILABLE，提示用户先 backfill。"""
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=[]))

    r = client.post(
        "/backtest",
        headers=auth_headers,
        json={
            "strategy_id": "sma_cross",
            "symbol": "BTC/USDT",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-05T00:00:00Z",
        },
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "NO_BARS_AVAILABLE"
    assert "backfill" in body["message"]
