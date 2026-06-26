"""``live_runner.LiveRunnerManager._process_bar`` 集成测试（D-11）。

直接喂一根 bar（不走轮询 / 不打网络），断言下单意图走完护栏内 plan/exec 链路：
生成 plan（approved_by=system:live_runner）+ 落 orders / positions + 更新 run 进度。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn
from inalpha_shared.errors import InalphaError

from inalpha_paper.config import get_paper_settings
from inalpha_paper.engine.live_session import LiveEngineSession
from inalpha_paper.fills import apply_fill_to_positions_and_cash
from inalpha_paper.kernel.identifiers import ClientOrderId
from inalpha_paper.live_runner import LiveRunnerManager, _closed_bars
from inalpha_paper.model.orders import (
    GUARD_ORDER_PREFIX,
    Order,
    OrderSide,
    OrderType,
)
from inalpha_paper.storage import accounts as accounts_store
from inalpha_paper.storage import closed_trades as closed_trades_store
from inalpha_paper.storage import orders as orders_store
from inalpha_paper.storage import positions as positions_store
from inalpha_paper.storage import strategy_candidates as candidates_store
from inalpha_paper.storage import strategy_runs as runs_store
from inalpha_paper.strategy.base import Strategy

from .test_live_session import (
    _INSTRUMENT,
    _bar,
    _BuyOnceStrategy,
    _PosTrackStrategy,
    _StopOrderStrategy,
)

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


async def _insert_run(account_id, candidate_id=None):  # type: ignore[no-untyped-def]
    """插一行 run；candidate_id=None 时先建一个真候选（strategy_runs.candidate_id 有 FK）。"""
    async with get_conn() as conn:
        if candidate_id is None:
            # 结构可区分 salt 作 STRING 字面量（非注释）：结构指纹去重剥注释后会让
            # 注释-only / 注释-salt 候选全撞成同一个 → 同 candidate 第二次起跑 409。
            candidate_id, _ = await candidates_store.insert_candidate(
                conn, code=f'"live-runner test candidate {uuid4().hex}"\n'
            )
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
    run = await _insert_run(account_id)

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
    assert d["intent"] == "open_long"  # 空仓 BUY → 开多（补 side 缺失的多空语义）
    assert float(d["bar_close"]) == 50_000.0
    assert d["plan_id"] is not None
    assert d["order_id"] is not None
    assert d["fill_price"] is not None


async def test_process_bar_unsupported_order_records_rejected_decision(
    app_with_lifespan: Any,
) -> None:
    """不支持单型（STOP_MARKET）：不落单、记一行 rejected 决策让运维可见（issue #43）、run 不挂。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = LiveEngineSession(
        strategy_cls=_StopOrderStrategy, instrument_id=_INSTRUMENT, timeframe="1h",
        params={}, initial_cash=10_000.0, fee_rate=0.001,
    )
    account_id = uuid4()
    run = await _insert_run(account_id)

    await manager._process_bar(session, run, _bar(1_700_000_000_000_000_000, close=50_000.0))

    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
        decisions = await runs_store.list_decisions(conn, run["id"])
        run_fresh = await runs_store.get(conn, run["id"])
    assert orders == []  # 不支持单型不落单
    assert len(decisions) == 1
    d = decisions[0]
    assert d["outcome"] == "rejected"
    assert d["intent"] == "open_short"  # 空仓 SELL STOP → 开空意图仍记录
    assert "STOP_MARKET" in d["reason"] and "not supported" in d["reason"]
    assert d["order_id"] is None  # 没真下单 → 无 order/plan 交叉引用
    assert d["plan_id"] is None
    assert run_fresh["status"] == "running"  # 不杀 run


async def test_process_bar_circuit_break_on_global_lock(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """账户级（global scope）风控锁 → _process_bar 返 True（熔断信号）+ 记 risk_rejected（issue #44）。"""
    from inalpha_shared.errors import ConflictError

    async def fake_enforce(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise ConflictError(
            "account drawdown 15% exceeded", code="RISK_REJECTED",
            details={"lock_scope": "global", "rule_name": "MaxDrawdownRule"},
        )

    monkeypatch.setattr("inalpha_paper.live_runner.risk_guard_mod.enforce", fake_enforce)

    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    account_id = uuid4()
    run = await _insert_run(account_id)

    circuit_break = await manager._process_bar(
        session, run, _bar(1_700_000_000_000_000_000, close=50_000.0)
    )

    assert circuit_break is True  # global 锁 → 熔断
    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
        decisions = await runs_store.list_decisions(conn, run["id"])
    assert orders == []
    assert decisions[0]["outcome"] == "risk_rejected"


async def test_process_bar_symbol_lock_does_not_circuit_break(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """symbol scope 锁（cooldown 等局部、会过）→ 不熔断（_process_bar 返 False）。"""
    from inalpha_shared.errors import ConflictError

    async def fake_enforce(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise ConflictError(
            "cooldown active", code="RISK_REJECTED",
            details={"lock_scope": "symbol", "rule_name": "CooldownRule"},
        )

    monkeypatch.setattr("inalpha_paper.live_runner.risk_guard_mod.enforce", fake_enforce)

    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    run = await _insert_run(uuid4())
    circuit_break = await manager._process_bar(
        _make_session(), run, _bar(1_700_000_000_000_000_000, close=50_000.0)
    )
    assert circuit_break is False  # 局部锁不熔断


async def test_run_loop_circuit_break_auto_stops(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """账户级熔断 → _run_loop auto-stop 置 stopped（非 errored），防僵尸 run（issue #44）。"""
    from inalpha_shared.errors import ConflictError

    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def fake_build(_r):  # type: ignore[no-untyped-def]
        return _make_session(), None

    calls = {"n": 0}

    async def fake_fetch(_r):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            return _bar(1_700_000_000_000_000_000, close=50_000.0)
        raise asyncio.CancelledError  # 守护：若没 auto-stop，第 2 次拉 bar 时干净退出

    async def fake_enforce(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise ConflictError(
            "account drawdown breached", code="RISK_REJECTED",
            details={"lock_scope": "global"},
        )

    monkeypatch.setattr(manager, "_build_session", fake_build)
    monkeypatch.setattr(manager, "_fetch_latest_bar", fake_fetch)
    monkeypatch.setattr("inalpha_paper.live_runner.risk_guard_mod.enforce", fake_enforce)
    monkeypatch.setattr("inalpha_paper.live_runner.asyncio.sleep", lambda _s: asyncio.sleep(0))

    await asyncio.wait_for(manager._run_loop(run), timeout=30.0)  # 抗负载 flaky：墙钟超时只为抓真 hang，重负载下 2s 太短(#90)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "stopped"  # 熔断是正常终态，非 errored
    assert calls["n"] == 1  # 处理完第一根就 auto-stop，没再拉第 2 根
    assert any("熔断" in e.get("msg", "") for e in (fresh["run_log"] or []))


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
    run = await _insert_run(account_id)

    await manager._process_bar(session, run, _bar(1_700_000_000_000_000_000, close=50_000.0))

    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
        decisions = await runs_store.list_decisions(conn, run["id"])
        run_fresh = await runs_store.get(conn, run["id"])
    assert orders == []  # 被风控拦下，未落单
    assert len(decisions) == 1
    assert decisions[0]["outcome"] == "risk_rejected"
    assert decisions[0]["intent"] == "open_long"  # 风控拒单也带 intent
    assert decisions[0]["reason"] == "cooldown active"
    assert run_fresh["status"] == "running"  # 拒单不杀 run
    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is None or pos.is_flat


async def test_route_failure_cleans_up_ee_orphan(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """护栏链路中途抛错 → reject_order 清 EE 孤儿单 + 异常上抛（CR medium）。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    run = await _insert_run(uuid4())

    # 撮合阶段抛意外错（模拟 DB / 执行瞬时故障，发生在 confirm_fill / reject 之前）
    def boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom in route")

    monkeypatch.setattr("inalpha_paper.live_runner.OrderExecutor.execute", boom)

    # 监视 reject_order 是否被调来清 EE 内存状态
    rejected: list = []
    orig_reject = session.reject_order

    def spy_reject(**kw):  # type: ignore[no-untyped-def]
        rejected.append(kw["order"])
        return orig_reject(**kw)

    monkeypatch.setattr(session, "reject_order", spy_reject)

    with pytest.raises(RuntimeError, match="boom in route"):
        await manager._process_bar(session, run, _bar(1_700_000_000_000_000_000, close=50_000.0))

    assert len(rejected) == 1  # 异常路径调了 reject_order 清 EE 孤儿
    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is None or pos.is_flat  # 没有幽灵持仓


async def test_stop_does_not_overwrite_errored(app_with_lifespan: Any) -> None:
    """stop 一个已 errored 的 run 不应把状态擦成 stopped（CR：保留崩溃痕迹）。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    run = await _insert_run(uuid4())
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
    run = await _insert_run(uuid4())

    await manager._run_loop(run)  # 应在 _build_session 前就 fail-closed 返回

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    assert any("风控不可用" in e.get("msg", "") for e in (fresh["run_log"] or []))


async def test_process_bar_no_signal_no_order(app_with_lifespan: Any) -> None:
    """策略不下单的 bar → 不产生任何 plan/order。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    account_id = uuid4()
    run = await _insert_run(account_id)

    # _BuyOnceStrategy 第一根就买；这里先喂一根买掉，再喂第二根（不再下单）
    await manager._process_bar(session, run, _bar(1_700_000_000_000_000_000, close=50_000.0))
    await manager._process_bar(session, run, _bar(1_700_003_600_000_000_000, close=51_000.0))

    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
    # 只有第一根那笔单，第二根没新增
    assert len(orders) == 1


# ─── 错误分类 / _run_loop 健壮性（issue #37.3 / #37.4）───


def test_is_retryable_classification() -> None:
    """4xx InalphaError 不可重试；网络 / 超时 / 未知错误可重试（issue #37.3）。"""
    from inalpha_shared.errors import ConflictError, NotFoundError, ValidationError

    from inalpha_paper.live_runner import _is_retryable

    assert _is_retryable(ValidationError("bad")) is False  # 400
    assert _is_retryable(NotFoundError("gone")) is False  # 404
    assert _is_retryable(ConflictError("dup")) is False  # 409
    assert _is_retryable(InalphaError("boom", status_code=500)) is True
    assert _is_retryable(TimeoutError("net")) is True
    assert _is_retryable(RuntimeError("x")) is True


async def test_run_loop_non_retryable_error_immediate_errored(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """不可重试错误（4xx InalphaError）→ 立即 errored，不等 streak（issue #37.3）。"""
    from inalpha_shared.errors import ValidationError

    # require=False 让 factory=None 也能过 fail-closed 门进主循环；streak 用默认（≥2）
    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    assert settings.live_max_error_streak >= 2  # 证明"立即"而非"攒够 streak"
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def fake_build(_r):  # type: ignore[no-untyped-def]
        return _make_session(), None

    async def fake_fetch(_r):  # type: ignore[no-untyped-def]
        raise ValidationError("symbol delisted", code="SYMBOL_DELISTED")

    monkeypatch.setattr(manager, "_build_session", fake_build)
    monkeypatch.setattr(manager, "_fetch_latest_bar", fake_fetch)

    await asyncio.wait_for(manager._run_loop(run), timeout=30.0)  # 抗负载 flaky：墙钟超时只为抓真 hang，重负载下 2s 太短(#90)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    assert any("ValidationError" in e.get("msg", "") for e in (fresh["run_log"] or []))


async def test_run_loop_retryable_error_accumulates_to_errored(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """可重试错误（网络 / 超时）：单次不杀 run，连续达 streak 才 errored。"""
    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False, "live_max_error_streak": 2}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def fake_build(_r):  # type: ignore[no-untyped-def]
        return _make_session(), None

    async def fake_fetch(_r):  # type: ignore[no-untyped-def]
        raise TimeoutError("network blip")  # 非 InalphaError → 可重试

    async def no_sleep(_s):  # type: ignore[no-untyped-def]
        return None  # 退避置空：loop 第 2 次错时 errored 退出，不真等

    monkeypatch.setattr(manager, "_build_session", fake_build)
    monkeypatch.setattr(manager, "_fetch_latest_bar", fake_fetch)
    monkeypatch.setattr("inalpha_paper.live_runner.asyncio.sleep", no_sleep)

    await asyncio.wait_for(manager._run_loop(run), timeout=30.0)  # 抗负载 flaky：墙钟超时只为抓真 hang，重负载下 2s 太短(#90)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    # 攒到 streak=2 才挂：≥2 条网络错（证明第 1 次没杀 run）
    blips = [e for e in (fresh["run_log"] or []) if "network blip" in e.get("msg", "")]
    assert len(blips) >= 2


async def test_run_loop_cancelled_clean_exit(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """CancelledError（stop 触发）→ 干净退出、run 不置 errored。"""
    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def fake_build(_r):  # type: ignore[no-untyped-def]
        return _make_session(), None

    async def fake_fetch(_r):  # type: ignore[no-untyped-def]
        raise asyncio.CancelledError

    monkeypatch.setattr(manager, "_build_session", fake_build)
    monkeypatch.setattr(manager, "_fetch_latest_bar", fake_fetch)

    with pytest.raises(asyncio.CancelledError):
        await manager._run_loop(run)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "running"  # 干净退出，不标 errored


async def test_done_callback_marks_loop_crashed(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """_run_loop 自己的错误处理路径写库失败 → done_callback 兜底置 errored（issue #67）。

    场景：build 抛不可重试错 → loop 走"立即 errored"写库，但第一次 set_status
    本身抛错（模拟 DB 抖动；append 已被 savepoint 隔离不会逃出，set_status 是
    唯一未受保护的关键写）→ 异常逃出 _run_loop。修复前 run 永卡 running。
    """
    from inalpha_shared.errors import ValidationError

    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def fake_build(_r):  # type: ignore[no-untyped-def]
        raise ValidationError("bad candidate", code="BAD_CANDIDATE")  # 不可重试

    real_set_status = runs_store.set_status
    calls = {"n": 0}

    async def flaky_set_status(conn, rid, status, **kw):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db blip: first set_status failed")
        return await real_set_status(conn, rid, status, **kw)

    monkeypatch.setattr(manager, "_build_session", fake_build)
    monkeypatch.setattr(
        "inalpha_paper.live_runner.runs_store.set_status", flaky_set_status
    )

    manager.start(run)
    task = manager._tasks[run["id"]]
    with pytest.raises(RuntimeError):  # 证明异常确实逃出了 _run_loop
        await asyncio.wait_for(task, timeout=30.0)  # 抗负载 flaky：墙钟超时只为抓真 hang，重负载下 2s 太短(#90)

    # 兜底写库在 done_callback 另起的 task 里：轮询等它落库
    fresh = None
    for _ in range(100):
        async with get_conn() as conn:
            fresh = await runs_store.get(conn, run["id"])
        if fresh is not None and fresh["status"] == "errored":
            break
        await asyncio.sleep(0.02)
    assert fresh is not None and fresh["status"] == "errored"
    assert any(
        "run loop crashed" in e.get("msg", "") for e in (fresh["run_log"] or [])
    )


async def test_build_errored_path_survives_log_write_failure(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """build 失败路径的 append 已 savepoint 隔离：日志写不进也第一跳直接置 errored，
    异常不再逃出 _run_loop 绕 done_callback 兜底（PR review）。"""
    from inalpha_shared.errors import ValidationError

    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def fake_build(_r):  # type: ignore[no-untyped-def]
        raise ValidationError("bad candidate", code="BAD_CANDIDATE")  # 不可重试

    async def always_fail_append(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("db partial outage: error_log 永远写不进")

    monkeypatch.setattr(manager, "_build_session", fake_build)
    monkeypatch.setattr(
        "inalpha_paper.live_runner.runs_store.append_error_log", always_fail_append
    )

    await asyncio.wait_for(manager._run_loop(run), timeout=30.0)  # 抗负载 flaky：墙钟超时只为抓真 hang，重负载下 2s 太短(#90)  # 不抛 = 异常没逃出

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"  # 日志丢了，状态第一跳就落了


async def test_mark_loop_crashed_sets_errored_even_if_log_write_fails(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """append_error_log **恒**失败 → set_status 仍执行，run 置 errored（PR review）。

    与上一个测试的差别：上面是"第一次失败、兜底时成功"；这里日志写入永远失败
    （持续 DB 局部故障），savepoint 隔离保证 set_status 不被连带跳过——否则 run
    又卡回 running，与 #67 同根因。
    """
    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def always_fail_append(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("db partial outage: error_log 永远写不进")

    monkeypatch.setattr(
        "inalpha_paper.live_runner.runs_store.append_error_log", always_fail_append
    )

    await manager._mark_loop_crashed(run["id"], RuntimeError("boom"))

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"  # 日志丢了，但状态没卡 running


async def test_set_status_only_if_running_guards_terminal_state(
    app_with_lifespan: Any,
) -> None:
    """only_if_status 原子守卫：终态不被 read-then-write 竞态覆盖（PR review）。

    模拟 stop() 与 loop_crashed 兜底穿插的危险方向：run 已 errored（crash 终态），
    迟到的 stop() 写 stopped 必须未命中——否则 crash 被静默埋掉。
    """
    run = await _insert_run(uuid4())
    async with get_conn() as conn:
        await runs_store.set_status(conn, run["id"], "errored")
        res = await runs_store.set_status(
            conn, run["id"], "stopped", only_if_status="running"
        )
        fresh = await runs_store.get(conn, run["id"])
    assert res is None  # 守卫未命中
    assert fresh["status"] == "errored"  # crash 终态保住了


async def test_done_callback_ignores_cancellation(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """stop() 的正常取消路径 → done_callback 不触发 loop_crashed 兜底（issue #67 回归）。"""
    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def fake_build(_r):  # type: ignore[no-untyped-def]
        return _make_session(), None

    async def fake_fetch(_r):  # type: ignore[no-untyped-def]
        await asyncio.sleep(60)
        return None

    monkeypatch.setattr(manager, "_build_session", fake_build)
    monkeypatch.setattr(manager, "_fetch_latest_bar", fake_fetch)

    manager.start(run)
    await asyncio.sleep(0.05)  # 让 loop 跑起来
    await manager.stop(run["id"])

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "stopped"
    assert not any(
        "run loop crashed" in e.get("msg", "") for e in (fresh["run_log"] or [])
    )


async def test_run_loop_build_session_failure_errored(app_with_lifespan: Any) -> None:
    """_build_session 失败（candidate 非法 / 未 promoted）→ run 置 errored。"""
    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    # _insert_run 造的 candidate code 非合法 Strategy（裸 STRING 字面量）、status='candidate'
    # → _build_session 加载/审计/契约校验抛错
    run = await _insert_run(uuid4())

    await asyncio.wait_for(manager._run_loop(run), timeout=30.0)  # 抗负载 flaky：墙钟超时只为抓真 hang，重负载下 2s 太短(#90)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    assert any("build failed" in e.get("msg", "") for e in (fresh["run_log"] or []))


async def test_process_bar_not_filled_rejects(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """OrderExecutor 返非 FILLED（如 LIMIT 未成交）→ 落 rejected 决策 + reject_order，不建仓。"""
    from inalpha_paper.storage import positions as positions_store

    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    account_id = uuid4()
    run = await _insert_run(account_id)

    from datetime import UTC, datetime

    ts = 1_700_000_000_000_000_000

    # client_order_id 用 uuid 后缀：orders 表跨 pytest 进程持久化（conftest 只 truncate
    # risk_locks），硬编码 id 会让重复跑测试时 orders_pkey 主键冲突（测试隔离债）。
    unfilled_coid = f"limit-unfilled-{uuid4().hex[:8]}"

    def fake_execute(**_kw):  # type: ignore[no-untyped-def]
        return {
            "client_order_id": unfilled_coid, "status": "REJECTED",
            "filled_quantity": 0.0, "avg_fill_price": 0.0, "fee": 0.0,
            "notional": 0.0, "ts_event": datetime.now(UTC),
            "rejection_reason": "limit not crossed",
        }

    monkeypatch.setattr("inalpha_paper.live_runner.OrderExecutor.execute", fake_execute)

    # 监视 confirm_fill 不应被调（未成交不能回灌成交）
    confirmed: list = []
    orig_confirm = session.confirm_fill

    def spy_confirm(**kw):  # type: ignore[no-untyped-def]
        confirmed.append(kw)
        return orig_confirm(**kw)

    monkeypatch.setattr(session, "confirm_fill", spy_confirm)

    await manager._process_bar(session, run, _bar(ts, close=50_000.0))

    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
        positions = await positions_store.list_by_account(conn, account_id)
        decisions = await runs_store.list_decisions(conn, run["id"])
    assert len(orders) == 1 and orders[0]["status"] == "REJECTED"
    assert positions == []  # 未成交不建仓
    assert confirmed == []  # 没调 confirm_fill
    assert len(decisions) == 1 and decisions[0]["outcome"] == "rejected"
    assert decisions[0]["reason"] == "limit not crossed"
    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is None or pos.is_flat


async def test_mark_running_as_errored_reconcile(app_with_lifespan: Any) -> None:
    """服务重启 reconcile：把残留 running 标 errored，非 running 不动（issue #37.4）。"""
    running_run = await _insert_run(uuid4())
    stopped_run = await _insert_run(uuid4())
    async with get_conn() as conn:
        await runs_store.set_status(conn, stopped_run["id"], "stopped")

    async with get_conn() as conn:
        n = await runs_store.mark_running_as_errored(conn, reason="service restart reconcile")
    assert n >= 1

    async with get_conn() as conn:
        f_running = await runs_store.get(conn, running_run["id"])
        f_stopped = await runs_store.get(conn, stopped_run["id"])
    assert f_running["status"] == "errored"
    assert f_stopped["status"] == "stopped"  # 非 running 不受影响
    assert any("reconcile" in e.get("msg", "") for e in (f_running["run_log"] or []))


async def test_compute_run_pnl_from_db_realized_plus_unrealized(
    app_with_lifespan: Any,
) -> None:
    """DB 派生 PnL（issue #45）= 已实现（closed_trades）+ 未实现（持仓 MtM），crypto 本地 FX。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    run = await _insert_run(account_id)  # started_at = NOW()

    async with get_conn() as conn:
        await accounts_store.get_or_create(conn, account_id)  # base USD
        # 建持仓：BUY 1 @ 100（currency USDT）→ qty=1 avg=100
        await positions_store.apply_fill(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT",
            side="BUY", fill_qty=Decimal("1"), fill_price=Decimal("100"),
            ts_event=datetime.now(UTC), order_id="pnl-open", currency="USDT",
        )
        # 已实现 50（一笔平仓，close_ts 在 run.started_at 之后）
        await closed_trades_store.insert_close(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT", side="long",
            open_ts=datetime.now(UTC), close_ts=datetime.now(UTC),
            open_price=Decimal("100"), close_price=Decimal("150"), quantity=Decimal("1"),
            close_profit_pct=0.5, close_profit_abs=50.0, exit_reason="signal",
            open_order_id="x", close_order_id="y",
        )
        await conn.commit()
        run = await runs_store.get(conn, run["id"])

        # mark=120 → 未实现 (120-100)*1 = 20；总 = 50 + 20 = 70（计价货币 USDT）
        # M-1：DB 读（_read_run_pnl_quote）与 FX 折算（_convert_run_pnl_to_base）分两段，
        # 折算在连接上下文外做；这里同 _process_bar 的调用顺序。
        quote_total, currency, base = await manager._read_run_pnl_quote(conn, run, 120.0)

    assert float(quote_total) == pytest.approx(70.0)
    pnl = await manager._convert_run_pnl_to_base(run, quote_total, currency, base)
    assert pnl is not None
    assert float(pnl) == pytest.approx(70.0)  # USDT→USD 本地 1.0


async def test_compute_run_pnl_deducts_fees(app_with_lifespan: Any) -> None:
    """净盈亏口径：cumulative_pnl = 毛已实现 + 毛未实现 - run 期间手续费（issue #45 follow-up）。

    手续费在成交时已从 cash 扣，但 close_profit_abs / 未实现是毛口径不含费；不补回展示
    盈亏会让高频策略 cumulative_pnl 虚高（用户实测发现）。同时验证 REJECTED 单 fee 不计入。
    """
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    run = await _insert_run(account_id)  # started_at = NOW()

    async with get_conn() as conn:
        await accounts_store.get_or_create(conn, account_id)  # base USD
        # 持仓 BUY 1 @ 100 → mark=120 未实现 20；一笔平仓已实现 50 → 毛 = 70
        await positions_store.apply_fill(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT",
            side="BUY", fill_qty=Decimal("1"), fill_price=Decimal("100"),
            ts_event=datetime.now(UTC), order_id="fee-open", currency="USDT",
        )
        await closed_trades_store.insert_close(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT", side="long",
            open_ts=datetime.now(UTC), close_ts=datetime.now(UTC),
            open_price=Decimal("100"), close_price=Decimal("150"), quantity=Decimal("1"),
            close_profit_pct=0.5, close_profit_abs=50.0, exit_reason="signal",
            open_order_id="x", close_order_id="y",
        )
        # 两笔 FILLED 单手续费合计 5 → 净 = 70 - 5 = 65
        for oid, fee in (("fee-1", Decimal("2")), ("fee-2", Decimal("3"))):
            await orders_store.insert(
                conn, account_id=account_id, client_order_id=f"{oid}-{account_id}",
                venue="binance", symbol="BTC/USDT", side="BUY", order_type="MARKET",
                quantity=Decimal("1"), price=None, status="FILLED",
                filled_quantity=Decimal("1"), avg_fill_price=Decimal("100"),
                fee=fee, notional=Decimal("100"), ts_event=datetime.now(UTC),
            )
        # REJECTED 单 fee=9 不应计入（sum_fees 只统计 status='FILLED'）
        await orders_store.insert(
            conn, account_id=account_id, client_order_id=f"rej-1-{account_id}",
            venue="binance", symbol="BTC/USDT", side="BUY", order_type="LIMIT",
            quantity=Decimal("1"), price=Decimal("1"), status="REJECTED",
            filled_quantity=Decimal("0"), avg_fill_price=None,
            fee=Decimal("9"), notional=Decimal("0"), ts_event=datetime.now(UTC),
        )
        await conn.commit()
        run = await runs_store.get(conn, run["id"])

        quote_total, _currency, _base = await manager._read_run_pnl_quote(conn, run, 120.0)

    # 毛 70 - FILLED 手续费 5 = 净 65；REJECTED 的 9 被过滤掉
    assert float(quote_total) == pytest.approx(65.0)


async def test_ttl_exceeded_stops_run(app_with_lifespan: Any) -> None:
    """运行超过 max_runtime_s → auto-stop 置 stopped + error_log 记 TTL（issue #44 TTL 兜底）。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    run = await _insert_run(account_id)
    # started_at 远在过去，max_runtime_s=60 → 超时熔断
    exceeded = await manager._ttl_exceeded(run["id"], datetime(2020, 1, 1, tzinfo=UTC), 60)
    assert exceeded is True
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "stopped"
    assert any("TTL" in e.get("msg", "") for e in (fresh["run_log"] or []))


async def test_ttl_disabled_when_zero_or_no_start(app_with_lifespan: Any) -> None:
    """max_runtime_s=0（默认）或 started_at 缺失 → 永不超时（返 False，不动 run）。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    run = await _insert_run(uuid4())
    assert await manager._ttl_exceeded(run["id"], datetime(2020, 1, 1, tzinfo=UTC), 0) is False
    assert await manager._ttl_exceeded(run["id"], None, 3600) is False
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "running"  # 未被改动


def test_classify_build_error_categories() -> None:
    """build 错误分类：data 5xx→infra 重试 / 4xx→strategy 不重试 / RuntimeError→strategy / 其它→unknown。"""
    from inalpha_paper.data_client import DataServiceError
    from inalpha_paper.live_runner import _classify_build_error

    # DataServiceError（status 502，含网络不可达）→ 退避重试
    assert _classify_build_error(DataServiceError("unreachable")) == ("infra_unavailable", True)
    # InalphaError 4xx（symbol 非法等确定性）→ 立即 errored
    assert _classify_build_error(InalphaError("bad", status_code=400)) == ("strategy_error", False)
    # RuntimeError（candidate 缺失 / AST / 契约）→ 立即 errored
    assert _classify_build_error(RuntimeError("not promoted")) == ("strategy_error", False)
    # 未知（DB 瞬时错等）→ 保守重试
    assert _classify_build_error(ValueError("?")) == ("unknown", True)


async def _noop_sleep(*_a: Any, **_kw: Any) -> None:
    """monkeypatch asyncio.sleep：build 退避测试里跳过真等待。"""


async def test_build_non_retryable_errors_immediately(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """build 抛不可重试错（策略代码 RuntimeError）→ 立即 errored，error_log 带 code=strategy_error（#41）。"""
    settings = get_paper_settings().model_copy(update={"live_runner_require_risk_guard": False})
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    async def boom(_run: Any) -> Any:
        raise RuntimeError("candidate code failed AST audit")

    monkeypatch.setattr(manager, "_build_session", boom)
    await manager._run_loop(run)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    assert any(e.get("code") == "strategy_error" for e in (fresh["run_log"] or []))


async def test_build_retryable_backs_off_then_errored(
    app_with_lifespan: Any, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """build 可重试错（data 不可达 502）→ 退避攒 streak 到上限 → errored，code=infra_unavailable（#41）。"""
    from inalpha_paper.data_client import DataServiceError

    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False, "live_max_error_streak": 2}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    run = await _insert_run(uuid4())

    calls = 0

    async def flaky(_run: Any) -> Any:
        nonlocal calls
        calls += 1
        raise DataServiceError("data-service unreachable")

    monkeypatch.setattr(manager, "_build_session", flaky)
    monkeypatch.setattr("inalpha_paper.live_runner.asyncio.sleep", _noop_sleep)
    await manager._run_loop(run)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    assert calls >= 2  # 退避重试过（不是一次就死）
    assert any(e.get("code") == "infra_unavailable" for e in (fresh["run_log"] or []))


async def test_restore_position_from_db_brings_session_to_position(
    app_with_lifespan: Any,
) -> None:
    """resume 桥接（issue #37.2）：_restore_position 从 DB 读持仓 → 灌回 session。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    async with get_conn() as conn:
        await positions_store.apply_fill(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT",
            side="BUY", fill_qty=Decimal("2"), fill_price=Decimal("100"),
            ts_event=datetime.now(UTC), order_id="restore-open", currency="USDT",
        )
        await conn.commit()

    session = _make_session()  # 起始空仓
    run = {
        "id": uuid4(), "account_id": account_id, "venue": "binance",
        "symbol": "BTC/USDT", "last_bar_ts": datetime.now(UTC),
    }
    await manager._restore_position(session, run)

    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is not None and not pos.is_flat
    assert pos.quantity == 2.0


async def test_convert_run_pnl_zero_short_circuits_no_network() -> None:
    """total_quote=0 → 直接返 Decimal(0)，不打 /fx（非 USD 币种也不需网络）。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    run = {"id": uuid4(), "account_id": uuid4()}
    # EUR→USD 本需网络；若没短路会构造 DataClient 打 HTTP。0 应直接短路返 0。
    pnl = await manager._convert_run_pnl_to_base(run, Decimal(0), "EUR", "USD")
    assert pnl == Decimal(0)


async def test_restore_position_from_db_short_position(app_with_lifespan: Any) -> None:
    """resume 桥接空头路径（M-2）：DB 负 qty 持仓 → restore 后 portfolio 与策略视图都是 short。

    空头续跑被误恢复成多头后果重（下一根 bar 意图反向 → 可能反向单），故无人值守路径
    必须有测试守住"负 qty → SELL 方向合成成交 → portfolio.quantity 仍为负"。
    """
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    async with get_conn() as conn:
        # SELL 2 @ 100 从空仓开空 → 有符号 qty = -2（storage 层纯有符号累积，不挡裸 short）
        row, _close = await positions_store.apply_fill(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT",
            side="SELL", fill_qty=Decimal("2"), fill_price=Decimal("100"),
            ts_event=datetime.now(UTC), order_id="restore-short-open", currency="USDT",
        )
        await conn.commit()
    assert float(row["quantity"]) == -2.0  # DB 确实是空头

    # 用持仓追踪策略，验证策略视图也被 prime 成 short（on_position_opened 收到负 qty）
    session = LiveEngineSession(
        strategy_cls=_PosTrackStrategy, instrument_id=_INSTRUMENT, timeframe="1h",
        params={}, initial_cash=10_000.0, fee_rate=0.001,
    )
    run = {
        "id": uuid4(), "account_id": account_id, "venue": "binance",
        "symbol": "BTC/USDT", "last_bar_ts": datetime.now(UTC),
    }
    await manager._restore_position(session, run)

    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is not None and not pos.is_flat
    assert pos.quantity == -2.0  # 恢复为 short，未被误判成 long
    assert session._strategy.opened_qty == -2.0  # type: ignore[attr-defined]


async def test_restore_position_skips_when_flat(app_with_lifespan: Any) -> None:
    """无持仓（DB 无该 symbol 行）→ _restore_position no-op，session 保持空仓。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    run = {
        "id": uuid4(), "account_id": uuid4(), "venue": "binance",
        "symbol": "BTC/USDT", "last_bar_ts": datetime.now(UTC),
    }
    await manager._restore_position(session, run)
    pos = session.portfolio.position(_INSTRUMENT)
    assert pos is None or pos.is_flat


async def test_list_all_running_returns_running_only(app_with_lifespan: Any) -> None:
    """resume 查询（issue #46）：list_all_running 只返 running，stopped/errored 不返。"""
    r_stopped = await _insert_run(uuid4())
    async with get_conn() as conn:
        await runs_store.set_status(conn, r_stopped["id"], "stopped")
    r_running = await _insert_run(uuid4())

    async with get_conn() as conn:
        running = await runs_store.list_all_running(conn)
    ids = {r["id"] for r in running}
    assert r_running["id"] in ids
    assert r_stopped["id"] not in ids


# ─── 现货 long-only 网关守门（禁裸空 / 禁超卖翻空）───


class _SellOnceStrategy(Strategy):
    """第一根 bar 市价卖 ``sell_qty`` 单位（测 spot long-only 守门用）。"""

    def __init__(  # type: ignore[no-untyped-def]
        self, name, clock, msgbus, instrument_id, timeframe, sell_qty=1.0, **_kw
    ) -> None:
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._sell_qty = sell_qty
        self._sent = False

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Any) -> None:
        if not self._sent:
            self._sent = True
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId(f"sell-{uuid4().hex[:8]}"),
                    instrument_id=self._instrument_id,
                    side=OrderSide.SELL,
                    type=OrderType.MARKET,
                    quantity=self._sell_qty,
                )
            )


def _sell_session(sell_qty: float) -> LiveEngineSession:
    return LiveEngineSession(
        strategy_cls=_SellOnceStrategy, instrument_id=_INSTRUMENT, timeframe="1h",
        params={"sell_qty": sell_qty}, initial_cash=10_000.0, fee_rate=0.001,
    )


async def test_process_bar_naked_short_rejected(app_with_lifespan: Any) -> None:
    """空仓现货 SELL → spot long-only 守门拒（禁裸空）：不落单 / 不建空仓 / 记 rejected / run 不挂。

    复现 d4404933 漂移根因:OrderExecutor 无状态不查持仓,若不守门则空仓 SELL 会成交成裸空。
    """
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _sell_session(1.0)
    account_id = uuid4()
    run = await _insert_run(account_id)

    await manager._process_bar(session, run, _bar(1_700_000_000_000_000_000, close=50_000.0))

    async with get_conn() as conn:
        orders = await orders_store.list_by_account(conn, account_id)
        positions = await positions_store.list_by_account(conn, account_id)
        decisions = await runs_store.list_decisions(conn, run["id"])
        run_fresh = await runs_store.get(conn, run["id"])

    assert orders == []  # 裸空不落账
    assert positions == []  # 不建空仓
    assert len(decisions) == 1
    d = decisions[0]
    assert d["outcome"] == "rejected"
    assert d["side"] == "SELL"
    assert d["intent"] == "open_short"  # 空仓 SELL 判 open_short，但被守门拒
    assert "INSUFFICIENT_POSITION" in d["reason"]
    assert d["order_id"] is None and d["plan_id"] is None  # 没真下单 → 无交叉引用
    assert run_fresh is not None and run_fresh["status"] == "running"  # 不杀 run


async def test_process_bar_oversell_no_flip_to_short(app_with_lifespan: Any) -> None:
    """持多 1.0 后 SELL 2.0（超卖）→ 守门拒，持仓维持 +1.0 不翻空（d4404933 漂移不变量）。

    守门读 DB 持仓(权威),即便新 session 视图为空仓也按真实 +1.0 判,拒掉会翻空的那笔。
    对齐回测 ``test_backtest_e2e.py`` 的 ``pos.quantity >= 0`` 不变量。
    """
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    run = await _insert_run(account_id)

    # bar1：_BuyOnceStrategy 建多 1.0
    await manager._process_bar(
        _make_session(), run, _bar(1_700_000_000_000_000_000, close=50_000.0)
    )
    # bar2：另起 session 提交 SELL 2.0（超过 DB 实际持仓 1.0）
    await manager._process_bar(
        _sell_session(2.0), run, _bar(1_700_000_003_600_000_000, close=49_000.0)
    )

    async with get_conn() as conn:
        pos = await positions_store.get(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT"
        )
    assert pos is not None
    assert Decimal(str(pos["quantity"])) == Decimal("1.0")  # 维持多仓，未翻空


# ─── 框架保护性出场遇 session/DB 视图分叉：钳到实仓全平（#109 CR medium）───


class _ProtectiveSellStrategy(Strategy):
    """第一根 bar 提交一笔框架保护性出场单（``guard-`` 前缀 + ``stop_loss`` tag），量 = sell_qty。

    三因子（side=SELL + 保护性 tag + guard 前缀）→ ``is_protective_order`` 判真，复现
    PositionGuard 触发的灾难止损在 live runner 路径上的撮合行为。
    """

    def __init__(  # type: ignore[no-untyped-def]
        self, name, clock, msgbus, instrument_id, timeframe, sell_qty=1.0, **_kw
    ) -> None:
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._sell_qty = sell_qty
        self._sent = False

    def on_start(self) -> None:
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar: Any) -> None:
        if not self._sent:
            self._sent = True
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId(f"{GUARD_ORDER_PREFIX}{uuid4().hex[:8]}"),
                    instrument_id=self._instrument_id,
                    side=OrderSide.SELL,
                    type=OrderType.MARKET,
                    quantity=self._sell_qty,
                    tag="stop_loss",
                )
            )


def _protective_sell_session(sell_qty: float) -> LiveEngineSession:
    return LiveEngineSession(
        strategy_cls=_ProtectiveSellStrategy, instrument_id=_INSTRUMENT, timeframe="1h",
        params={"sell_qty": sell_qty}, initial_cash=10_000.0, fee_rate=0.001,
    )


async def test_protective_exit_clamps_to_position_on_divergence(
    app_with_lifespan: Any,
) -> None:
    """保护性出场量 > DB 实仓（session/DB 分叉）→ 钳到实仓全平，不超卖翻空、不整单拒。

    场景：bar1 建多 1.0；bar2 另一路径（模拟同账户 HTTP /orders/submit）卖掉 0.5 → DB=0.5；
    bar3 框架保护性出场按策略视图 SELL 1.0。守门读 DB=0.5，若整单拒则实仓 0.5 无止损保护、
    暴露持续扩大（#109 CR medium）；若照单成交则翻空 −0.5（裸空）。正确行为：钳到 0.5 全平。
    """
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    run = await _insert_run(account_id)

    # bar1：建多 1.0
    await manager._process_bar(
        _make_session(), run, _bar(1_700_000_000_000_000_000, close=50_000.0)
    )
    # bar2：另一 session 卖掉 0.5（模拟同账户被别的写路径减仓 → DB 落到 0.5）
    await manager._process_bar(
        _sell_session(0.5), run, _bar(1_700_000_003_600_000_000, close=50_000.0)
    )
    # bar3：框架保护性出场 SELL 1.0（策略视图仍记 1.0，但 DB 实仓只剩 0.5）
    await manager._process_bar(
        _protective_sell_session(1.0), run, _bar(1_700_000_007_200_000_000, close=49_000.0)
    )

    async with get_conn() as conn:
        pos = await positions_store.get(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT"
        )
        orders = await orders_store.list_by_account(conn, account_id)
        decisions = await runs_store.list_decisions(conn, run["id"])
        run_fresh = await runs_store.get(conn, run["id"])

    # 钳量全平：DB 实仓 → 0（既非 −0.5 裸空，也非维持 0.5 无保护）
    assert pos is not None
    assert Decimal(str(pos["quantity"])) == Decimal("0")
    # 保护性出场单（bar3 收盘 49000 撮合）按钳后量 0.5 落账、成交
    guard_filled = [
        o for o in orders
        if o["side"] == "SELL"
        and o["status"] == "FILLED"
        and Decimal(str(o["avg_fill_price"])) == Decimal("49000")
    ]
    assert len(guard_filled) == 1
    assert Decimal(str(guard_filled[0]["quantity"])) == Decimal("0.5")  # 落账量 = 钳后量
    assert Decimal(str(guard_filled[0]["filled_quantity"])) == Decimal("0.5")
    # 决策复盘：本笔保护性出场记 filled（不是 rejected），且 quantity = 钳后量 0.5
    # （与 orders 落账同源，不是策略意图量 1.0——否则复盘面板与落账对不上）
    assert decisions[-1]["outcome"] == "filled"
    assert Decimal(str(decisions[-1]["quantity"])) == Decimal("0.5")
    assert run_fresh is not None and run_fresh["status"] == "running"


async def test_protective_exit_on_flat_position_still_rejected(
    app_with_lifespan: Any,
) -> None:
    """DB 实仓为 0（已全平）时的保护性出场 → 仍拒单、不翻空：钳量豁免不放过裸空。

    钳量只在「有实仓可平」时生效；实仓为 0 无可钳 → 走权威闸 raise → rejected，
    DB 维持 0（绝不因 is_protective_exit 而放过裸空 −1.0）。
    """
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    run = await _insert_run(account_id)

    # bar1：建多 1.0；bar2：全平 1.0 → DB=0
    await manager._process_bar(
        _make_session(), run, _bar(1_700_000_000_000_000_000, close=50_000.0)
    )
    await manager._process_bar(
        _sell_session(1.0), run, _bar(1_700_000_003_600_000_000, close=50_000.0)
    )
    # bar3：实仓已 0，保护性出场 SELL 1.0
    await manager._process_bar(
        _protective_sell_session(1.0), run, _bar(1_700_000_007_200_000_000, close=49_000.0)
    )

    async with get_conn() as conn:
        pos = await positions_store.get(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT"
        )
        decisions = await runs_store.list_decisions(conn, run["id"])
        run_fresh = await runs_store.get(conn, run["id"])

    # 维持平仓，未翻空
    assert pos is not None
    assert Decimal(str(pos["quantity"])) == Decimal("0")
    assert decisions[-1]["outcome"] == "rejected"
    assert "INSUFFICIENT_POSITION" in decisions[-1]["reason"]
    assert run_fresh is not None and run_fresh["status"] == "running"


async def test_protective_exit_clamp_preserves_high_precision(
    app_with_lifespan: Any,
) -> None:
    """钳量全平用精确 Decimal（locked_qty）落账，高精度持仓不留浮点微尘仓位。

    持仓 NUMERIC 列无精度上限，可存超 float 精度的量（高精度 altcoin / 合约乘数）。若钳量走
    Decimal→float→Decimal 往返，``apply_fill`` 减去的量 ≠ 原持仓 → 留极小残差（≠0）误触后续
    守门。本例直接建一个超 float 精度的持仓，保护性出场钳量全平，断言持仓**精确归零**。
    """
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    run = await _insert_run(account_id)

    # 直接建一个超 float 精度（18 位小数）的多仓——绕开 strategy 路径以精确控制持仓量
    hi_qty = Decimal("1.123456789012345678")
    seed_ts = datetime(2023, 11, 14, tzinfo=UTC)
    async with get_conn() as conn, conn.transaction():
        await accounts_store.get_or_create(conn, account_id)
        await apply_fill_to_positions_and_cash(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT",
            side="BUY", quantity=hi_qty, fill_price=Decimal("100"),
            fee=Decimal("0"), ts_event=seed_ts, order_id="seed-hiprec",
        )

    # 保护性出场 SELL 2.0（> hi_qty）→ 钳到 hi_qty 全平
    await manager._process_bar(
        _protective_sell_session(2.0), run, _bar(1_700_000_000_000_000_000, close=100.0)
    )

    async with get_conn() as conn:
        pos = await positions_store.get(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT"
        )
    # 精确归零：钳量走 Decimal(locked_qty)，无 float 往返微尘（旧 float 往返会留 ≠0 残差）
    assert pos is not None
    assert Decimal(str(pos["quantity"])) == Decimal("0")
