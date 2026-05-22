"""D-8c · 端到端测试：strategy_hint → compose → run_backtest → list_backtest_runs。

不挂 research-service，直接构造 ResearchPlan 风格的 hint 字典丢给 compose；
端到端验证血缘字段从 strategy_hint 传到 backtest_runs 表再回到 GET 查询。
"""
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


def _bars(n: int = 100) -> list[dict[str, Any]]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        make_bar_row(
            (base + timedelta(hours=i)).isoformat(),
            close=100 + 10 * math.sin(2 * math.pi * i / 20),
        )
        for i in range(n)
    ]


@respx.mock
def test_compose_to_backtest_to_history(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """完整闭环：mock research 给的 hint → compose → backtest → list 拿回历史。"""
    research_id = uuid4()
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars(100))
    )

    # Step 1: mock research 给 hint —— 调 compose
    hint = {
        "family": "trend",
        "params": {"fast_period": 5, "slow_period": 15, "trade_size": 0.05},
        "reasoning": "强 momentum + 上穿信号",
    }
    factors = [
        {
            "name": "sma5_cross_15",
            "kind": "momentum",
            "value": 1.02,
            "strength": 0.8,
            "horizon": "intraday",
            "explanation": "5-bar SMA crossed 15-bar from below",
        }
    ]
    r = client.post(
        "/strategies/compose",
        headers=auth_headers,
        json={"hint": hint, "factors": factors, "timeframe": "1h"},
    )
    assert r.status_code == 200, r.text
    composed = r.json()
    assert composed["strategy_id"] == "sma_cross"
    strategy_id = composed["strategy_id"]
    params = composed["params"]

    # Step 2: run_backtest 用 compose 的输出 + 透传 research_id / strategy_hint
    r = client.post(
        "/backtest",
        headers=auth_headers,
        json={
            "strategy_id": strategy_id,
            "params": params,
            "venue": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-05T00:00:00Z",
            "initial_cash": 10_000.0,
            "fee_rate": 0.001,
            "research_id": str(research_id),
            "strategy_hint": hint,
        },
    )
    assert r.status_code == 200, r.text
    backtest = r.json()
    assert backtest["research_id"] == str(research_id)
    assert backtest["run_id"] is not None
    run_id = backtest["run_id"]
    UUID(run_id)
    # 振荡市 SMA cross 必然触发交易
    assert backtest["num_trades"] >= 2

    # Step 3: list_backtest_runs by research_id 应能拉回这条
    r = client.get(
        f"/backtest_runs?research_id={research_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    found = next((row for row in rows if row["run_id"] == run_id), None)
    assert found is not None
    # 验证血缘和指标都还原
    assert found["research_id"] == str(research_id)
    assert found["strategy_code"] == "sma_cross"
    # strategy_hint 已写入审计字段
    assert found["strategy_hint"] is not None
    assert found["strategy_hint"]["family"] == "trend"
    # metrics 完整可读
    assert "sharpe" in found["metrics"]
    assert "max_drawdown_pct" in found["metrics"]


@respx.mock
def test_compose_rejects_neutral_hint_no_backtest(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """family='none' 的 hint → compose 拒绝 → 不应跑 backtest（前端 / agent 路由职责）。"""
    r = client.post(
        "/strategies/compose",
        headers=auth_headers,
        json={
            "hint": {"family": "none", "params": {}, "reasoning": "分析师分歧"},
            "factors": [],
            "timeframe": "1h",
        },
    )
    assert r.status_code == 200
    composed = r.json()
    assert composed["strategy_id"] is None
    assert composed["rejected_reason"] is not None
    # agent 应基于 strategy_id=None 决定不跑回测 —— 测试不实际验证 agent 行为，
    # 但断言 compose 的契约让 agent 能正确分支
