"""``live_runner.LiveRunnerManager._process_bar`` 集成测试（D-11）。

直接喂一根 bar（不走轮询 / 不打网络），断言下单意图走完护栏内 plan/exec 链路：
生成 plan（approved_by=system:live_runner）+ 落 orders / positions + 更新 run 进度。
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn

from inalpha_paper.config import get_paper_settings
from inalpha_paper.engine.live_session import LiveEngineSession
from inalpha_paper.live_runner import LiveRunnerManager
from inalpha_paper.storage import orders as orders_store
from inalpha_paper.storage import positions as positions_store
from inalpha_paper.storage import strategy_runs as runs_store
from inalpha_paper.strategy.base import Strategy

from .test_live_session import _INSTRUMENT, _bar, _BuyOnceStrategy

pytestmark = pytest.mark.integration


def _make_session() -> LiveEngineSession:
    return LiveEngineSession(
        strategy_cls=_BuyOnceStrategy,
        instrument_id=_INSTRUMENT,
        timeframe="1h",
        params={},
        initial_cash=10_000.0,
        fee_rate=0.001,
    )


async def _insert_run(account_id, candidate_id):  # type: ignore[no-untyped-def]
    async with get_conn() as conn:
        return await runs_store.insert(
            conn, candidate_id=candidate_id, account_id=account_id,
            venue="binance", symbol="BTC/USDT", timeframe="1h", params={},
        )


async def test_process_bar_routes_through_plan_exec(app_with_lifespan: Any) -> None:
    """喂一根触发 BUY 的 bar → plan/exec 落账 + 持仓出现 + plan 机器审批。"""
    # factory=None → 风控 fail-open（测试不接 risk_rules）
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    account_id = uuid4()
    candidate_id = uuid4()
    run = await _insert_run(account_id, candidate_id)

    await manager._process_bar(session, run, _bar(1_700_000_000_000_000_000, close=50_000.0))

    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
        positions = await positions_store.list_by_account(conn, account_id)
        cur = await conn.execute(
            "SELECT approved_by, rationale, status FROM trade_plans WHERE account_id = %s",
            (str(account_id),),
        )
        plan_rows = await cur.fetchall()
        run_fresh = await runs_store.get(conn, run["id"])

    # 落了一笔 FILLED 的 BUY 单
    assert len(orders) == 1
    assert orders[0]["status"] == "FILLED"
    assert orders[0]["side"] == "BUY"
    # 持仓出现（BTC/USDT）
    assert len(positions) == 1
    assert positions[0]["symbol"] == "BTC/USDT"
    assert float(positions[0]["quantity"]) == 1.0
    # plan 机器审批 + 审计可追溯
    assert len(plan_rows) == 1
    assert plan_rows[0]["approved_by"] == "system:live_runner"
    assert plan_rows[0]["status"] == "executed"
    assert f"run:{run['id']}" in plan_rows[0]["rationale"]
    # run 进度更新
    assert run_fresh is not None
    assert run_fresh["last_bar_ts"] is not None


class _CountingStrategy(Strategy):
    """记录每根 bar 的 close，用于验证预热喂了历史 bar。"""

    def __init__(self, name, clock, msgbus, instrument_id, timeframe, **_kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self.closes_seen: list[float] = []

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar) -> None:  # type: ignore[no-untyped-def]
        self.closes_seen.append(bar.close)


async def test_warmup_feeds_history_without_trading(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """_warmup_session 拉 N 根历史 bar 喂策略建立指标，丢弃 order、持仓保持空仓。"""
    # mock data /bars：返 5 根递增 close 的 bar dict（不打网络）
    async def fake_get_bars(self, **kwargs):  # type: ignore[no-untyped-def]
        return [
            {
                "ts": f"2026-06-01T0{i}:00:00Z", "open": 100.0 + i, "high": 100.0 + i,
                "low": 100.0 + i, "close": 100.0 + i, "volume": 1.0,
                "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h",
            }
            for i in range(5)
        ]

    monkeypatch.setattr("inalpha_paper.data_client.DataClient.get_bars", fake_get_bars)

    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = LiveEngineSession(
        strategy_cls=_CountingStrategy, instrument_id=_INSTRUMENT, timeframe="1h",
        params={}, initial_cash=10_000.0, fee_rate=0.001,
    )
    run = {"account_id": uuid4(), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"}

    last_ts = await manager._warmup_session(session, run)

    # 策略看到了 5 根历史 bar（指标已预热）
    assert session._strategy.closes_seen == [100.0, 101.0, 102.0, 103.0, 104.0]  # type: ignore[attr-defined]
    # 预热不真下单 → 持仓保持空仓
    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is None or pos.is_flat
    # 返回最后一根预热 bar 的 ts（供 _run_loop 去重）
    assert last_ts is not None


async def test_process_bar_no_signal_no_order(app_with_lifespan: Any) -> None:
    """策略不下单的 bar → 不产生任何 plan/order。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    account_id = uuid4()
    run = await _insert_run(account_id, uuid4())

    # _BuyOnceStrategy 第一根就买；这里先喂一根买掉，再喂第二根（不再下单）
    await manager._process_bar(session, run, _bar(1_700_000_000_000_000_000, close=50_000.0))
    await manager._process_bar(session, run, _bar(1_700_003_600_000_000_000, close=51_000.0))

    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
    # 只有第一根那笔单，第二根没新增
    assert len(orders) == 1
