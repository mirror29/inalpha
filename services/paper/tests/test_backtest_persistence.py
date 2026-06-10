"""D-8c · 回测落库 + 血缘字段 + 历史查询集成测试。"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from .conftest import make_bar_row

pytestmark = pytest.mark.integration


def _bars_oscillating(n: int = 100) -> list[dict[str, Any]]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    out = []
    for i in range(n):
        close = 100 + 10 * math.sin(2 * math.pi * i / 20)
        out.append(make_bar_row((base + timedelta(hours=i)).isoformat(), close=close))
    return out


def _backtest_payload(research_id: UUID | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "strategy_id": "sma_cross",
        "params": {"fast_period": 5, "slow_period": 15, "trade_size": 0.05},
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "from_ts": "2026-01-01T00:00:00Z",
        "to_ts": "2026-01-05T00:00:00Z",
        "initial_cash": 10_000.0,
        "fee_rate": 0.001,
    }
    if research_id is not None:
        payload["research_id"] = str(research_id)
        payload["strategy_hint"] = {
            "family": "trend",
            "params": {"fast_period": 5, "slow_period": 15},
            "reasoning": "smoke test",
        }
    return payload


# ────────────────────────────────────────────────────────────────────
# POST /backtest 落库 + 返回 run_id
# ────────────────────────────────────────────────────────────────────


@respx.mock
def test_backtest_writes_run_id_and_params_hash(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    research_id = uuid4()
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars_oscillating(100))
    )

    r = client.post(
        "/backtest",
        headers=auth_headers,
        json=_backtest_payload(research_id=research_id),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # 关键新字段
    assert body["run_id"] is not None
    UUID(body["run_id"])  # 校验是合法 UUID
    assert body["research_id"] == str(research_id)
    assert body["params_hash"] is not None
    assert len(body["params_hash"]) == 16


@respx.mock
def test_backtest_works_without_research_id(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """不带 research_id 时仍能落库（research_id=None）+ 返回 run_id。"""
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars_oscillating(100))
    )

    r = client.post(
        "/backtest",
        headers=auth_headers,
        json=_backtest_payload(research_id=None),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_id"] is not None
    assert body["research_id"] is None


@respx.mock
def test_same_params_yields_same_hash(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """同 strategy + 同 params → 同 params_hash（去重用）。"""
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars_oscillating(100))
    )

    r1 = client.post(
        "/backtest", headers=auth_headers, json=_backtest_payload()
    )
    r2 = client.post(
        "/backtest", headers=auth_headers, json=_backtest_payload()
    )
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["params_hash"] == r2.json()["params_hash"]
    # 不同 run_id（两次落库）
    assert r1.json()["run_id"] != r2.json()["run_id"]


# ────────────────────────────────────────────────────────────────────
# GET /backtest_runs 历史查询
# ────────────────────────────────────────────────────────────────────


@respx.mock
def test_list_backtest_runs_by_research(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """跑两次相同 research_id 的回测 → GET 应返回两条。"""
    research_id = uuid4()
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars_oscillating(100))
    )

    # 先跑两次
    for _ in range(2):
        r = client.post(
            "/backtest",
            headers=auth_headers,
            json=_backtest_payload(research_id=research_id),
        )
        assert r.status_code == 200

    # 查
    r = client.get(
        f"/backtest_runs?research_id={research_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 2
    for row in rows[:2]:
        assert row["research_id"] == str(research_id)
        assert row["strategy_code"] == "sma_cross"
        assert "metrics" in row
        assert "sharpe" in row["metrics"]
        assert row["status"] == "done"


@respx.mock
def test_list_backtest_runs_by_strategy_code(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars_oscillating(100))
    )

    r = client.post("/backtest", headers=auth_headers, json=_backtest_payload())
    assert r.status_code == 200

    r = client.get(
        "/backtest_runs?strategy_code=sma_cross&limit=5",
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json()
    assert any(row["strategy_code"] == "sma_cross" for row in rows)


def test_list_backtest_runs_without_filter_returns_recent(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """不带 filter → 全局最近 N 条（控制台 Agent 活动流用），不再 400。"""
    r = client.get("/backtest_runs", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    # 前面的用例已落过 sma_cross 的 run,全局列表应能看到。
    assert any(row["strategy_code"] == "sma_cross" for row in rows)


def test_list_backtest_runs_requires_auth(client: TestClient) -> None:
    r = client.get(f"/backtest_runs?research_id={uuid4()}")
    assert r.status_code == 401


@respx.mock
def test_research_id_not_found_returns_empty_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """不存在的 research_id 应返回 [] 而非 404。"""
    r = client.get(
        f"/backtest_runs?research_id={uuid4()}",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json() == []


# ────────────────────────────────────────────────────────────────────
# GET /backtest_runs/{id}/trades 逐笔成交（含每笔盈亏）
# ────────────────────────────────────────────────────────────────────


@respx.mock
def test_backtest_run_trades_recorded(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """回测落库的逐笔成交可经 endpoint 读回，条数 == num_trades，含盈亏/意图。"""
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars_oscillating(100))
    )
    r = client.post("/backtest", headers=auth_headers, json=_backtest_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    run_id = body["run_id"]
    assert body["num_trades"] >= 2

    tr = client.get(f"/backtest_runs/{run_id}/trades", headers=auth_headers)
    assert tr.status_code == 200, tr.text
    trades = tr.json()
    # 每笔成交一行，seq 从 0 连续递增
    assert len(trades) == body["num_trades"]
    assert [t["seq"] for t in trades] == list(range(len(trades)))
    for t in trades:
        assert t["side"] in ("BUY", "SELL")
        assert t["intent"] in ("open_long", "open_short", "close")
        assert t["fill_price"] is not None
        assert t["realized_pnl"] is not None
    # 现货 long-only：首笔空仓买入 = 开多；振荡市必有平仓
    assert trades[0]["side"] == "BUY"
    assert trades[0]["intent"] == "open_long"
    assert any(t["intent"] == "close" for t in trades)


def test_backtest_run_trades_empty_for_unknown_run(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """未知 run_id → 空数组（不报错）。"""
    r = client.get(f"/backtest_runs/{uuid4()}/trades", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == []
