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
from inalpha_paper.live_runner import LiveRunnerManager, _closed_bars
from inalpha_paper.storage import accounts as accounts_store
from inalpha_paper.storage import closed_trades as closed_trades_store
from inalpha_paper.storage import orders as orders_store
from inalpha_paper.storage import positions as positions_store
from inalpha_paper.storage import strategy_candidates as candidates_store
from inalpha_paper.storage import strategy_runs as runs_store
from inalpha_paper.strategy.base import Strategy

from .test_live_session import _INSTRUMENT, _bar, _BuyOnceStrategy, _StopOrderStrategy

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
            candidate_id, _ = await candidates_store.insert_candidate(
                conn, code=f"# live-runner test candidate {uuid4().hex}\n"
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

    await asyncio.wait_for(manager._run_loop(run), timeout=2.0)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "stopped"  # 熔断是正常终态，非 errored
    assert calls["n"] == 1  # 处理完第一根就 auto-stop，没再拉第 2 根
    assert any("熔断" in e.get("error", "") for e in (fresh["error_log"] or []))


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
    assert any("风控不可用" in e.get("error", "") for e in (fresh["error_log"] or []))


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

    await asyncio.wait_for(manager._run_loop(run), timeout=2.0)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    assert any("ValidationError" in e.get("error", "") for e in (fresh["error_log"] or []))


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

    await asyncio.wait_for(manager._run_loop(run), timeout=2.0)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    # 攒到 streak=2 才挂：≥2 条网络错（证明第 1 次没杀 run）
    blips = [e for e in (fresh["error_log"] or []) if "network blip" in e.get("error", "")]
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


async def test_run_loop_build_session_failure_errored(app_with_lifespan: Any) -> None:
    """_build_session 失败（candidate 非法 / 未 promoted）→ run 置 errored。"""
    settings = get_paper_settings().model_copy(
        update={"live_runner_require_risk_guard": False}
    )
    manager = LiveRunnerManager(risk_guard_factory=None, settings=settings)
    # _insert_run 造的 candidate code 只是注释、status='candidate' → _build_session 抛错
    run = await _insert_run(uuid4())

    await asyncio.wait_for(manager._run_loop(run), timeout=2.0)

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh["status"] == "errored"
    assert any("build failed" in e.get("error", "") for e in (fresh["error_log"] or []))


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
    assert any("reconcile" in e.get("error", "") for e in (f_running["error_log"] or []))


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

        # mark=120 → 未实现 (120-100)*1 = 20；总 = 50 + 20 = 70（USDT→USD 本地 1.0）
        pnl = await manager._compute_run_pnl(conn, await runs_store.get(conn, run["id"]), 120.0)

    assert pnl is not None
    assert float(pnl) == pytest.approx(70.0)


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
