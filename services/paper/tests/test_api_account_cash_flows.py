"""账户外生资金事件(充值/重置/流水)+ perp 跨仓保证金聚合 集成测试。

资金变更一律"流水行 + 余额更新同事务":充值不改 initial_cash,重置删持仓、
现金回基准、历史订单保留。perp 守门从单笔 IM vs 全钱包升级为跨仓聚合
(其他仓已占 IM + 本笔目标 IM + fee ≤ 钱包)。
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.account_id import account_id_from_sub
from inalpha_paper.storage import strategy_candidates as candidates_store
from inalpha_paper.storage import strategy_runs as runs_store

from .conftest import fresh_account_token

pytestmark = pytest.mark.integration


def _headers(prefix: str = "cf") -> tuple[str, dict[str, str]]:
    sub, token = fresh_account_token(prefix)
    return sub, {"Authorization": f"Bearer {token}"}


def test_deposit_records_flow_and_updates_balance(client: TestClient) -> None:
    """充值:余额更新 + 流水留痕;initial_cash 不变(充值 ≠ 赚钱)。"""
    _, headers = _headers()
    r = client.post(
        "/accounts/me/deposit", headers=headers, json={"amount": 5_000.0}
    )
    assert r.status_code == 200, r.json()
    flow = r.json()
    assert flow["kind"] == "deposit"
    assert flow["currency"] == "USD"  # 默认 base_currency
    assert flow["amount"] == pytest.approx(5_000.0)
    assert flow["balance_after"] == pytest.approx(15_000.0)

    acct = client.get("/accounts/me", headers=headers).json()
    assert acct["cash_balances"]["USD"] == pytest.approx(15_000.0)
    assert acct["initial_cash"] == pytest.approx(10_000.0)  # 基准不随充值动
    # 净外生入金:真实收益 = equity − initial_cash − net_external_flows,充值不算盈利
    assert acct["net_external_flows"] == pytest.approx(5_000.0)

    flows = client.get("/accounts/me/cash_flows", headers=headers).json()
    assert len(flows) == 1 and flows[0]["kind"] == "deposit"


def test_deposit_non_base_currency_bucket(client: TestClient) -> None:
    """指定币种充值进对应桶(如 USDT),折算总现金随之增加。"""
    _, headers = _headers()
    r = client.post(
        "/accounts/me/deposit",
        headers=headers,
        json={"amount": 1_000.0, "currency": "USDT"},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["currency"] == "USDT"
    assert r.json()["balance_after"] == pytest.approx(1_000.0)

    acct = client.get("/accounts/me", headers=headers).json()
    assert acct["cash_balances"]["USDT"] == pytest.approx(1_000.0)
    assert acct["cash"] == pytest.approx(11_000.0)  # USD 10000 + USDT 1000×1.0


def test_deposit_invalid_amount_rejected(client: TestClient) -> None:
    _, headers = _headers()
    r = client.post("/accounts/me/deposit", headers=headers, json={"amount": -1})
    assert r.status_code == 400


def test_deposit_unknown_currency_rejected(client: TestClient) -> None:
    """未知币种 → 422(任意字符串会建出 FX 永远折算不了的垃圾桶)。"""
    _, headers = _headers()
    r = client.post(
        "/accounts/me/deposit",
        headers=headers,
        json={"amount": 100.0, "currency": "FOO"},
    )
    assert r.status_code == 422, r.json()
    assert r.json()["code"] == "UNSUPPORTED_CURRENCY"


def test_reset_clears_positions_keeps_history(client: TestClient) -> None:
    """重置:删持仓 + 现金回基准 + reset 流水;订单历史保留(审计不可抹)。

    已实现盈亏走 closed_trades + reset epoch 口径:重置前 500,重置后归零——
    快照与成交审计源不再分叉(此前从 positions 行汇总,删行即凭空归零)。
    """
    _, headers = _headers()
    # 买 0.1 @50000,平 0.05 @60000 → 已实现 +500(毛),持仓剩 0.05
    r = client.post(
        "/orders/submit", headers=headers,
        json={"symbol": "BTC/USDT", "side": "BUY", "type": "MARKET",
              "quantity": 0.1, "ref_price": 50_000.0},
    )
    assert r.status_code == 200, r.json()
    r = client.post(
        "/orders/submit", headers=headers,
        json={"symbol": "BTC/USDT", "side": "SELL", "type": "MARKET",
              "quantity": 0.05, "ref_price": 60_000.0},
    )
    assert r.status_code == 200, r.json()
    pre = client.get("/accounts/me", headers=headers).json()
    assert pre["realized_pnl"] == pytest.approx(500.0)
    assert client.get("/positions", headers=headers).json() != []

    r = client.post("/accounts/me/reset", headers=headers, json={})
    assert r.status_code == 200, r.json()
    flow = r.json()
    assert flow["kind"] == "reset"
    assert flow["balance_after"] == pytest.approx(10_000.0)
    assert "旧现金桶" in (flow["note"] or "")

    acct = client.get("/accounts/me", headers=headers).json()
    assert acct["cash_balances"] == {"USD": 10_000.0}  # USDT 负桶被清
    assert acct["positions_value"] == pytest.approx(0.0)
    # 新一轮口径:净外生入金与已实现盈亏都按"最近一次 reset 之后"统计 → 归零
    assert acct["net_external_flows"] == pytest.approx(0.0)
    assert acct["realized_pnl"] == pytest.approx(0.0)
    assert client.get("/positions", headers=headers).json() == []
    # 历史订单仍在(审计):重置不抹交易流水
    orders = client.get(
        "/orders", headers=headers, params={"symbol": "BTC/USDT"}
    ).json()
    assert len(orders) == 2


async def test_reset_blocked_by_running_run(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """有 running run 时重置 → 409(runner 下一根 bar 会把仓开回来)。"""
    sub, headers = _headers("cfrun")
    account_id = account_id_from_sub(sub)
    async with get_conn() as conn:
        cid, _ = await candidates_store.insert_candidate(
            conn, code=f'"cash-flow reset test candidate {uuid4().hex}"\n'
        )
        await runs_store.insert(
            conn, candidate_id=cid, account_id=account_id,
            venue="binance", symbol="BTC/USDT", timeframe="1h", params={},
        )
    r = client.post("/accounts/me/reset", headers=headers, json={})
    assert r.status_code == 409, r.json()
    assert r.json()["code"] == "ACCOUNT_HAS_RUNNING_RUNS"


async def test_trade_repo_window_clamped_by_reset(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """风控成交窗口按 reset epoch 收口:旧亏损不再触发新一轮的行为型规则。

    重置前 lookback 窗口内有一笔亏损平仓 → repo.refresh 能看到;reset 后同一窗口
    refresh → 看不到(旧成交属上一轮口径,MaxDrawdown/LowProfit 不该再用它锁账户)。
    """
    from datetime import UTC, datetime

    from inalpha_shared import db as shared_db

    from inalpha_paper.execution.risk_rules.postgres_repo import (
        PostgresTradeRepository,
    )
    from inalpha_paper.storage import closed_trades as closed_trades_store

    sub, headers = _headers("cfrepo")
    account_id = account_id_from_sub(sub)
    now = datetime.now(UTC)
    async with get_conn() as conn:
        await closed_trades_store.insert_close(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT",
            side="long", open_ts=now, close_ts=now,
            open_price=100.0, close_price=90.0, quantity=1.0,
            close_profit_pct=-10.0, close_profit_abs=-10.0,
            exit_reason="stop_loss",
        )
    assert shared_db._pool is not None
    repo = PostgresTradeRepository(account_id, shared_db._pool, lookback_min=1440)
    assert await repo.refresh() == 1  # 重置前:亏损在窗口内

    r = client.post("/accounts/me/reset", headers=headers, json={})
    assert r.status_code == 200, r.json()
    assert await repo.refresh() == 0  # 重置后:窗口起点被 reset epoch 收口


def test_perp_cross_position_margin_aggregated(client: TestClient) -> None:
    """perp 跨仓保证金聚合:多仓合计 IM 不得超钱包(单笔各自过闸的洞已堵)。

    钱包 10000 USDT:仓 A(BTC 0.15@50000, 1×)占 IM 7500;仓 B(ETH 1@3000, 1×)
    单笔 IM 3000 < 钱包,但 7500+3000+fee > 钱包 → 拒;缩到 0.5 → 放行。
    """
    _, headers = _headers("perpagg")
    r = client.post(
        "/accounts/me/deposit",
        headers=headers,
        json={"amount": 10_000.0, "currency": "USDT"},
    )
    assert r.status_code == 200, r.json()

    r_a = client.post(
        "/orders/submit", headers=headers,
        json={"symbol": "BTC/USDT:USDT", "side": "BUY", "type": "MARKET",
              "quantity": 0.15, "ref_price": 50_000.0,
              "trading_mode": "perp", "leverage": 1},
    )
    assert r_a.status_code == 200, r_a.json()
    assert r_a.json()["status"] == "FILLED"

    # 仓 B 单笔 IM(3000)本身 < 钱包,聚合后超 → 必须被拒(聚合前会放行,回归点)
    r_b = client.post(
        "/orders/submit", headers=headers,
        json={"symbol": "ETH/USDT:USDT", "side": "BUY", "type": "MARKET",
              "quantity": 1.0, "ref_price": 3_000.0,
              "trading_mode": "perp", "leverage": 1},
    )
    assert r_b.status_code == 409, r_b.json()
    body = r_b.json()
    assert body["code"] == "INSUFFICIENT_MARGIN"
    assert float(body["details"]["others_im"]) == pytest.approx(7_500.0)

    # 合计仍在钱包内的小仓 → 放行
    r_c = client.post(
        "/orders/submit", headers=headers,
        json={"symbol": "ETH/USDT:USDT", "side": "BUY", "type": "MARKET",
              "quantity": 0.5, "ref_price": 3_000.0,
              "trading_mode": "perp", "leverage": 1},
    )
    assert r_c.status_code == 200, r_c.json()
    assert r_c.json()["status"] == "FILLED"
