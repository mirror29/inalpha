"""`InMemoryLockStore` 单元测试 + RiskEngine 复用已有锁的行为。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from inalpha_paper.execution.exchange import EXECUTION_ENGINE_ENDPOINT
from inalpha_paper.execution.risk_engine import RiskEngine
from inalpha_paper.execution.risk_rules import (
    ClosedTradeRecord,
    CooldownRule,
    InMemoryLockStore,
    RiskVerdict,
)
from inalpha_paper.execution.risk_rules.base import Side
from inalpha_paper.kernel.clock import TestClock
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId, StrategyId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.commands import SubmitOrderCommand
from inalpha_paper.model.events import OrderRejected
from inalpha_paper.model.orders import Order, OrderSide, OrderType
from inalpha_paper.strategy.base import RISK_ENGINE_ENDPOINT

# ─── helpers ───


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _eth() -> InstrumentId:
    return InstrumentId(symbol="ETH/USDT", venue="binance")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _clock_at(dt: datetime) -> TestClock:
    return TestClock(initial_ns=int(dt.timestamp() * 1_000_000_000))


def _verdict(
    until: datetime,
    *,
    scope: str = "symbol",
    side: Side = "*",
    market: str | None = None,
    rule_name: str = "FakeRule",
) -> RiskVerdict:
    return RiskVerdict(
        until=until,
        reason="测试",
        rule_name=rule_name,
        lock_side=side,
        lock_scope=scope,  # type: ignore[arg-type]
        lock_market=market,
    )


# ─── InMemoryLockStore basic ───


def test_add_returns_lock_with_incremented_id() -> None:
    store = InMemoryLockStore()
    now = _utc(2026, 5, 26, 12, 0)
    until = _utc(2026, 5, 26, 13, 0)

    lock1 = store.add(_verdict(until), instrument_id=_btc(), now=now)
    lock2 = store.add(_verdict(until), instrument_id=_eth(), now=now)

    assert lock1.id == 1
    assert lock2.id == 2
    assert lock1.symbol == "BTC/USDT@binance"
    assert lock2.symbol == "ETH/USDT@binance"


def test_symbol_lock_carries_market_from_venue() -> None:
    store = InMemoryLockStore()
    now = _utc(2026, 5, 26, 12, 0)
    lock = store.add(_verdict(_utc(2026, 5, 26, 13)), instrument_id=_btc(), now=now)
    assert lock.scope == "symbol"
    assert lock.market == "binance"  # 自动从 InstrumentId.venue 派生


def test_global_lock_has_no_market_symbol() -> None:
    store = InMemoryLockStore()
    now = _utc(2026, 5, 26, 12, 0)
    lock = store.add(
        _verdict(_utc(2026, 5, 26, 13), scope="global"),
        instrument_id=None,
        now=now,
    )
    assert lock.scope == "global"
    assert lock.market is None
    assert lock.symbol is None


def test_market_lock_uses_verdict_market() -> None:
    store = InMemoryLockStore()
    now = _utc(2026, 5, 26, 12, 0)
    lock = store.add(
        _verdict(_utc(2026, 5, 26, 13), scope="market", market="nasdaq"),
        instrument_id=None,
        now=now,
    )
    assert lock.scope == "market"
    assert lock.market == "nasdaq"


# ─── list_active ───


def test_list_active_filters_expired() -> None:
    store = InMemoryLockStore()
    base = _utc(2026, 5, 26, 12, 0)
    store.add(_verdict(base + timedelta(minutes=10)), instrument_id=_btc(), now=base)
    store.add(_verdict(base + timedelta(minutes=120)), instrument_id=_eth(), now=base)

    after_10_min = base + timedelta(minutes=11)
    actives = store.list_active(after_10_min)
    assert len(actives) == 1
    assert actives[0].symbol == "ETH/USDT@binance"


def test_list_active_filter_by_scope() -> None:
    store = InMemoryLockStore()
    base = _utc(2026, 5, 26, 12, 0)
    until = base + timedelta(hours=1)
    store.add(_verdict(until), instrument_id=_btc(), now=base)
    store.add(_verdict(until, scope="global"), instrument_id=None, now=base)

    assert len(store.list_active(base, scope="global")) == 1
    assert len(store.list_active(base, scope="symbol")) == 1
    assert len(store.list_active(base)) == 2


# ─── is_locked ───


def test_is_locked_symbol_match() -> None:
    store = InMemoryLockStore()
    base = _utc(2026, 5, 26, 12, 0)
    until = base + timedelta(hours=1)
    store.add(_verdict(until), instrument_id=_btc(), now=base)

    assert store.is_locked(base, scope="symbol", symbol="BTC/USDT@binance") is not None
    assert store.is_locked(base, scope="symbol", symbol="ETH/USDT@binance") is None


def test_is_locked_global_match_any_symbol() -> None:
    store = InMemoryLockStore()
    base = _utc(2026, 5, 26, 12, 0)
    until = base + timedelta(hours=1)
    store.add(_verdict(until, scope="global"), instrument_id=None, now=base)

    assert store.is_locked(base, scope="global") is not None


def test_is_locked_side_intersects() -> None:
    """单边锁只拦同向（或 `*` 查询）。"""
    store = InMemoryLockStore()
    base = _utc(2026, 5, 26, 12, 0)
    until = base + timedelta(hours=1)
    store.add(_verdict(until, side="long"), instrument_id=_btc(), now=base)

    assert (
        store.is_locked(base, scope="symbol", symbol="BTC/USDT@binance", side="long")
        is not None
    )
    assert (
        store.is_locked(base, scope="symbol", symbol="BTC/USDT@binance", side="short")
        is None
    )
    # `*` 查询 hits 任何 lock
    assert (
        store.is_locked(base, scope="symbol", symbol="BTC/USDT@binance", side="*")
        is not None
    )


# ─── manual_unlock ───


def test_manual_unlock_removes_from_active() -> None:
    store = InMemoryLockStore()
    base = _utc(2026, 5, 26, 12, 0)
    until = base + timedelta(hours=1)
    lock = store.add(_verdict(until), instrument_id=_btc(), now=base)

    assert store.manual_unlock(lock.id, unlocked_by="admin", unlock_reason="测试") is True
    assert store.list_active(base) == []
    # 再次 unlock 同一 id 返 False
    assert store.manual_unlock(lock.id, unlocked_by="admin", unlock_reason="测试") is False


def test_manual_unlock_nonexistent_returns_false() -> None:
    store = InMemoryLockStore()
    assert (
        store.manual_unlock(9999, unlocked_by="admin", unlock_reason="x")
        is False
    )


# ─── RiskEngine 复用已有锁（行为测试）───


class _Repo:
    def __init__(self, trades: list[ClosedTradeRecord]) -> None:
        self._trades = trades

    def get_closed_trades(
        self, *, instrument_id: object = None, close_after: datetime, **_: object
    ) -> list[ClosedTradeRecord]:
        return [t for t in self._trades if t.close_ts >= close_after]


def test_riskengine_writes_to_lockstore_on_match() -> None:
    """rule 命中 → LockStore 多一条 active lock。"""
    bus = MessageBus()
    bus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, lambda _: None)

    now = _utc(2026, 5, 26, 12, 0)
    recent_trade = ClosedTradeRecord(
        instrument_id=_btc(),
        side="long",
        open_ts=_utc(2026, 5, 26, 11, 50),
        close_ts=_utc(2026, 5, 26, 11, 55),
        close_profit_pct=0.01,
        close_profit_abs=10.0,
        exit_reason="manual",
    )
    repo = _Repo([recent_trade])
    rule = CooldownRule({"stop_duration_min": 30, "lookback_min": 60}, repo)
    engine = RiskEngine(bus, rules=[rule], clock=_clock_at(now))

    assert engine.lock_store.list_active(now) == []
    cmd = SubmitOrderCommand(
        order=Order(
            client_order_id=ClientOrderId("c-1"),
            instrument_id=_btc(),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            quantity=1.0,
        ),
        strategy_id=StrategyId("test"),
        ts_init=1000,
    )
    bus.send(RISK_ENGINE_ENDPOINT, cmd)

    actives = engine.lock_store.list_active(now)
    assert len(actives) == 1
    assert actives[0].rule_name == "CooldownRule"


def test_riskengine_reuses_existing_lock_without_running_rules() -> None:
    """已锁 symbol 第二次 submit → 复用 lock，不重跑 rule。"""
    bus = MessageBus()
    forwarded: list[object] = []
    bus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, lambda m: forwarded.append(m))
    rejections: list[OrderRejected] = []
    bus.subscribe("events.order.test", lambda e: rejections.append(e))

    now = _utc(2026, 5, 26, 12, 0)
    recent_trade = ClosedTradeRecord(
        instrument_id=_btc(),
        side="long",
        open_ts=_utc(2026, 5, 26, 11, 50),
        close_ts=_utc(2026, 5, 26, 11, 55),
        close_profit_pct=0.01,
        close_profit_abs=10.0,
        exit_reason="manual",
    )
    repo = _Repo([recent_trade])
    rule = CooldownRule({"stop_duration_min": 30, "lookback_min": 60}, repo)
    engine = RiskEngine(bus, rules=[rule], clock=_clock_at(now))

    def _cmd(idx: int) -> SubmitOrderCommand:
        return SubmitOrderCommand(
            order=Order(
                client_order_id=ClientOrderId(f"c-{idx}"),
                instrument_id=_btc(),
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                quantity=1.0,
            ),
            strategy_id=StrategyId("test"),
            ts_init=1000 + idx,
        )

    # 第一次：写入 lock
    bus.send(RISK_ENGINE_ENDPOINT, _cmd(1))
    assert len(engine.lock_store.list_active(now)) == 1
    assert len(rejections) == 1
    assert "[CooldownRule]" in rejections[0].reason

    # 第二次：复用 lock，reason 标"已锁"
    bus.send(RISK_ENGINE_ENDPOINT, _cmd(2))
    assert len(engine.lock_store.list_active(now)) == 1  # 没新增
    assert len(rejections) == 2
    assert "已锁" in rejections[1].reason
    assert forwarded == []
