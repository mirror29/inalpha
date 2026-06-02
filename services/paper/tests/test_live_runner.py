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
from inalpha_paper.live_runner import LiveRunnerManager, _closed_bars
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

    # 决策复盘日志：记了一行 filled，交叉引用 plan/order
    async with get_conn() as conn:
        decisions = await runs_store.list_decisions(conn, run["id"])
    assert len(decisions) == 1
    d = decisions[0]
    assert d["outcome"] == "filled"
    assert d["side"] == "BUY"
    assert float(d["bar_close"]) == 50_000.0
    assert d["plan_id"] is not None
    assert d["order_id"] is not None
    assert d["fill_price"] is not None


async def test_process_bar_risk_rejected_records_decision(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """风控拒单：不落单、决策记 risk_rejected、run 不挂、持仓空仓。"""
    from inalpha_shared.errors import ConflictError

    async def fake_enforce(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise ConflictError("cooldown active", code="RISK_REJECTED")

    monkeypatch.setattr("inalpha_paper.live_runner.risk_guard_mod.enforce", fake_enforce)

    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    account_id = uuid4()
    run = await _insert_run(account_id, uuid4())

    await manager._process_bar(session, run, _bar(1_700_000_000_000_000_000, close=50_000.0))

    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
        decisions = await runs_store.list_decisions(conn, run["id"])
        run_fresh = await runs_store.get(conn, run["id"])
    assert orders == []  # 被风控拦下，未落单
    assert len(decisions) == 1
    assert decisions[0]["outcome"] == "risk_rejected"
    assert decisions[0]["reason"] == "cooldown active"
    assert run_fresh["status"] == "running"  # 拒单不杀 run
    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is None or pos.is_flat


async def test_stop_does_not_overwrite_errored(app_with_lifespan: Any) -> None:
    """stop 一个已 errored 的 run 不应把状态擦成 stopped（CR：保留崩溃痕迹）。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    run = await _insert_run(uuid4(), uuid4())
    async with get_conn() as conn:
        await runs_store.set_status(conn, run["id"], "errored")
    # run 不在 manager._tasks 里（没 start），stop 只会走 DB 分支
    await manager.stop(run["id"])
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"  # 未被覆盖成 stopped


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


def test_closed_bars_skips_forming_bar() -> None:
    """HIGH-1：最新一根未收盘的 bar 必须被丢弃，只返回已收盘的。"""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    def _raw(open_dt: datetime, close: float) -> dict:  # type: ignore[type-arg]
        return {
            "ts": open_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "open": close, "high": close, "low": close, "close": close, "volume": 1.0,
            "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h",
        }

    closed_open = now - timedelta(hours=2)   # open+1h <= now → 已收盘
    forming_open = now - timedelta(minutes=20)  # open+1h > now → 未收盘
    raw = [_raw(closed_open, 100.0), _raw(forming_open, 999.0)]

    out = _closed_bars(raw, _INSTRUMENT, "1h", now)
    # 只剩已收盘那根，未收盘（close=999）被丢
    assert len(out) == 1
    assert out[0].close == 100.0


async def test_run_loop_fail_closed_without_risk_guard(app_with_lifespan: Any) -> None:
    """HIGH-2：风控不可用（factory=None）且默认 require=True → _run_loop 拒跑置 errored。"""
    settings = get_paper_settings()
    assert settings.live_runner_require_risk_guard is True  # 默认 fail-closed
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4(), uuid4())

    await manager._run_loop(run)  # 应在 _build_session 前就 fail-closed 返回

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    assert any("风控不可用" in e.get("error", "") for e in (fresh["error_log"] or []))


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
