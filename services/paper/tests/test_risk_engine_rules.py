"""``RiskEngine`` 接入 `RiskRule` 后的行为。

测点：
- `rules=None` 时仍 pass-through（向后兼容）
- `rules=[CooldownRule]` 时未命中 → 转发
- `rules=[CooldownRule]` 时命中 → publish OrderRejected + 不转发
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from inalpha_paper.execution.exchange import EXECUTION_ENGINE_ENDPOINT
from inalpha_paper.execution.risk_engine import RiskEngine
from inalpha_paper.execution.risk_rules import ClosedTradeRecord, CooldownRule
from inalpha_paper.execution.risk_rules.base import Side
from inalpha_paper.kernel.clock import TestClock
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId, StrategyId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.commands import SubmitOrderCommand
from inalpha_paper.model.events import OrderRejected
from inalpha_paper.model.orders import Order, OrderSide, OrderType
from inalpha_paper.strategy.base import RISK_ENGINE_ENDPOINT

# ─── helpers ───


def _clock_at(dt: datetime) -> TestClock:
    ns = int(dt.timestamp() * 1_000_000_000)
    return TestClock(initial_ns=ns)


class _Repo:
    def __init__(self, trades: list[ClosedTradeRecord]) -> None:
        self._trades = trades

    def get_closed_trades(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        close_after: datetime,
        side: Side | None = None,
        exit_reasons: list[str] | None = None,
        max_profit_pct: float | None = None,
    ) -> list[ClosedTradeRecord]:
        out = self._trades
        if instrument_id is not None:
            out = [t for t in out if t.instrument_id == instrument_id]
        return [t for t in out if t.close_ts >= close_after]


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _make_submit_cmd() -> SubmitOrderCommand:
    return SubmitOrderCommand(
        order=Order(
            client_order_id=ClientOrderId("c-1"),
            instrument_id=_btc(),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            quantity=1.0,
        ),
        strategy_id=StrategyId("test-strategy"),
        ts_init=1000,
    )


# ─── 向后兼容：rules=None 时 pass-through ───


def test_pass_through_when_no_rules() -> None:
    """rules=None → 命令直接转发到 ExecutionEngine endpoint。"""
    bus = MessageBus()
    forwarded: list[object] = []
    bus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, lambda m: forwarded.append(m))

    _ = RiskEngine(bus)
    cmd = _make_submit_cmd()
    bus.send(RISK_ENGINE_ENDPOINT, cmd)

    assert forwarded == [cmd]


def test_pass_through_with_empty_rules_list() -> None:
    bus = MessageBus()
    forwarded: list[object] = []
    bus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, lambda m: forwarded.append(m))

    _ = RiskEngine(bus, rules=[])
    cmd = _make_submit_cmd()
    bus.send(RISK_ENGINE_ENDPOINT, cmd)

    assert forwarded == [cmd]


# ─── 配置一致性 ───


def test_rules_without_clock_raises() -> None:
    bus = MessageBus()
    repo = _Repo([])
    rule = CooldownRule({"stop_duration_min": 30}, repo)
    with pytest.raises(ValueError, match="必须同时提供 clock"):
        RiskEngine(bus, rules=[rule])


# ─── 未命中 → 转发 ───


def test_no_match_forwards() -> None:
    bus = MessageBus()
    forwarded: list[object] = []
    bus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, lambda m: forwarded.append(m))

    now = _utc(2026, 5, 26, 12, 0)
    repo = _Repo([])  # 无历史 trade
    rule = CooldownRule({"stop_duration_min": 30, "lookback_min": 60}, repo)
    _ = RiskEngine(bus, rules=[rule], clock=_clock_at(now))

    cmd = _make_submit_cmd()
    bus.send(RISK_ENGINE_ENDPOINT, cmd)

    assert forwarded == [cmd]


# ─── 命中 → publish OrderRejected 不转发 ───


def test_match_rejects_and_does_not_forward() -> None:
    bus = MessageBus()
    forwarded: list[object] = []
    bus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, lambda m: forwarded.append(m))

    rejections: list[OrderRejected] = []
    bus.subscribe("events.order.test-strategy", lambda e: rejections.append(e))

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
    _ = RiskEngine(bus, rules=[rule], clock=_clock_at(now))

    cmd = _make_submit_cmd()
    bus.send(RISK_ENGINE_ENDPOINT, cmd)

    assert forwarded == []  # 拒单，不转发
    assert len(rejections) == 1
    rej = rejections[0]
    assert rej.client_order_id == ClientOrderId("c-1")
    assert rej.strategy_id == StrategyId("test-strategy")
    assert "[CooldownRule]" in rej.reason
    assert "冷却期" in rej.reason


# ─── 多个 rule：先命中先返回，不继续跑后续 ───


def test_first_match_short_circuits() -> None:
    bus = MessageBus()
    forwarded: list[object] = []
    bus.register_endpoint(EXECUTION_ENGINE_ENDPOINT, lambda m: forwarded.append(m))

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
    rule1 = CooldownRule({"stop_duration_min": 10, "lookback_min": 60}, repo)
    rule2 = CooldownRule({"stop_duration_min": 60, "lookback_min": 60}, repo)
    engine = RiskEngine(bus, rules=[rule1, rule2], clock=_clock_at(now))

    assert engine.rule_names == ["CooldownRule", "CooldownRule"]

    cmd = _make_submit_cmd()
    bus.send(RISK_ENGINE_ENDPOINT, cmd)

    assert forwarded == []  # rule1 拒了
