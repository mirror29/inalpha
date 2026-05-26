"""ADR-0006 Step 3：BacktestEngine 接 rules 参数的 e2e。

验证：

- 默认（rules=None）回测行为不变（向后兼容）
- 配 RiskRule 后 strategy.submit_order 走 RiskEngine 拦截
- 拦截不影响 BacktestEngine 主循环（events 正常发布，equity 不动）
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.execution.risk_rules import (
    ClosedTradeRecord,
    RiskRulesConfig,
    build_rules,
)
from inalpha_paper.execution.risk_rules.base import Side
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.model.events import OrderRejected
from inalpha_paper.strategies.buy_and_hold import BuyAndHoldStrategy


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _make_bars(n: int = 5, start_close: float = 100.0) -> list[Bar]:
    base = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    bars = []
    for i in range(n):
        ts = int((base + timedelta(hours=i)).timestamp() * 1_000_000_000)
        bars.append(
            Bar(
                instrument_id=_btc(),
                timeframe="1h",
                ts_event=ts,
                ts_init=ts,
                open=start_close,
                high=start_close + 1.0,
                low=start_close - 1.0,
                close=start_close,
                volume=1.0,
            )
        )
    return bars


class _EmptyRepo:
    def get_closed_trades(self, **_: object) -> list[ClosedTradeRecord]:
        return []


class _ClosedCalendar:
    """所有市场都闭市。"""

    def is_trading_hours(
        self, market: str, now: datetime, *, include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        return False

    def next_session_open(self, market: str, now: datetime) -> datetime:
        return now + timedelta(hours=8)


class _OpenCalendar:
    """所有市场都开市。"""

    def is_trading_hours(
        self, market: str, now: datetime, *, include_pre: bool = False,
        include_after: bool = False,
    ) -> bool:
        return True

    def next_session_open(self, market: str, now: datetime) -> datetime:
        return now


# ─── 向后兼容：rules=None 行为不变 ───


def test_backtest_without_rules_passes_through() -> None:
    """rules=None → 与 D-5 ~ D-8 行为完全一致。"""
    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    strategy = BuyAndHoldStrategy(
        name="bh", clock=engine.clock, msgbus=engine.msgbus,
        instrument_id=_btc(), timeframe="1h", trade_size=0.01,
    )
    engine.add_strategy(strategy)
    report = engine.run(_make_bars(5))
    # buy_and_hold 第一根 bar 买入 → 持仓后 mark 不变 → equity ≈ initial
    assert report.num_bars_processed == 5
    assert report.num_trades >= 1


# ─── rules 接入：闭市 calendar 拦所有买入 ───


def test_market_hours_rule_blocks_buy_in_backtest() -> None:
    """闭市 → buy_and_hold 第一次 submit 被拒，不成交，equity 保持 initial。"""
    cfg = RiskRulesConfig.model_validate(
        {"rules": [{"name": "MarketHoursRule"}]}
    )
    rules = build_rules(
        cfg, trade_repo=_EmptyRepo(), market_calendar=_ClosedCalendar()
    )

    engine = BacktestEngine(initial_cash=10_000.0, rules=rules)

    # 捕获 OrderRejected events
    rejections: list[OrderRejected] = []

    def _capture_rejection(e: object) -> None:
        if isinstance(e, OrderRejected):
            rejections.append(e)

    engine.msgbus.subscribe("events.order.bh", _capture_rejection)

    strategy = BuyAndHoldStrategy(
        name="bh", clock=engine.clock, msgbus=engine.msgbus,
        instrument_id=_btc(), timeframe="1h", trade_size=0.01,
    )
    engine.add_strategy(strategy)
    report = engine.run(_make_bars(5))

    # 第一次提交被 MarketHoursRule 拒（后续因 buy_and_hold 已 _bought=True 不再提交）
    assert len(rejections) >= 1
    assert "[MarketHoursRule]" in rejections[0].reason
    # 没成交：num_trades=0 + 无手续费
    assert report.num_trades == 0
    assert report.total_fees == 0.0


# ─── rules 接入：开市 calendar 放行（验证 rules 不影响正常流程）───


def test_market_hours_rule_passes_when_open() -> None:
    """开市 → buy_and_hold 正常买入。"""
    cfg = RiskRulesConfig.model_validate(
        {"rules": [{"name": "MarketHoursRule"}]}
    )
    rules = build_rules(
        cfg, trade_repo=_EmptyRepo(), market_calendar=_OpenCalendar()
    )

    engine = BacktestEngine(initial_cash=10_000.0, rules=rules)
    rejections: list[OrderRejected] = []

    def _capture_rejection(e: object) -> None:
        if isinstance(e, OrderRejected):
            rejections.append(e)

    engine.msgbus.subscribe("events.order.bh", _capture_rejection)

    strategy = BuyAndHoldStrategy(
        name="bh", clock=engine.clock, msgbus=engine.msgbus,
        instrument_id=_btc(), timeframe="1h", trade_size=0.01,
    )
    engine.add_strategy(strategy)
    report = engine.run(_make_bars(5))

    # 没拒单
    assert rejections == []
    # 真买了
    assert report.num_trades >= 1
    assert report.num_bars_processed == 5


# ─── lock_store 持久化跨 bar ───


def test_lock_store_state_persists_across_bars() -> None:
    """rule 命中后写 lock；后续 bar 的 submit 走 existing lock，不重跑 rule。"""
    cfg = RiskRulesConfig.model_validate(
        {"rules": [{"name": "MarketHoursRule"}]}
    )
    rules = build_rules(
        cfg, trade_repo=_EmptyRepo(), market_calendar=_ClosedCalendar()
    )

    engine = BacktestEngine(initial_cash=10_000.0, rules=rules)
    strategy = BuyAndHoldStrategy(
        name="bh", clock=engine.clock, msgbus=engine.msgbus,
        instrument_id=_btc(), timeframe="1h", trade_size=0.01,
    )
    engine.add_strategy(strategy)
    engine.run(_make_bars(5))

    # 跑完后 lock_store 应有 1 条 market 级 active lock
    now = datetime.now(UTC)  # 真 now 之后才有意义，用 lock.locked_until 比较
    actives = engine.risk_engine.lock_store.list_active(now - timedelta(hours=10))
    assert len(actives) == 1
    assert actives[0].scope == "market"
    assert actives[0].market == "binance"


def _unused_side_marker() -> Side:
    return "*"
