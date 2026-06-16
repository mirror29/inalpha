"""``PositionGuard`` 单测（ADR-0052 框架级持仓保护止损）。

只测 guard 自身逻辑：给定持仓 + mark 序列，断言触发的 tag / 全平 / 峰值 / 关闭路径。
出场单提交走 ``EXECUTION_ENGINE_ENDPOINT``，这里注册一个 capture endpoint 验证提交。
"""
from __future__ import annotations

from inalpha_paper.engine.portfolio import Portfolio
from inalpha_paper.engine.position_guard import PositionGuard
from inalpha_paper.execution.exchange import EXECUTION_ENGINE_ENDPOINT
from inalpha_paper.kernel.clock import TestClock
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId, StrategyId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.commands import SubmitOrderCommand
from inalpha_paper.model.data import Bar
from inalpha_paper.model.events import OrderFilled
from inalpha_paper.model.orders import PROTECTIVE_EXIT_TAGS, OrderSide, OrderType

_SID = StrategyId("test")


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _bar(close: float, ts_ns: int = 1) -> Bar:
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def _long_portfolio(msgbus: MessageBus, qty: float, avg_price: float) -> Portfolio:
    """建一个持有 long 仓的 Portfolio（通过发一笔 BUY fill 走正常路径建仓）。"""
    pf = Portfolio(msgbus, initial_cash=1_000_000.0, fee_rate=0.0)
    fill = OrderFilled(
        client_order_id=ClientOrderId("setup"),
        strategy_id=_SID,
        ts_event=0,
        ts_init=0,
        instrument_id=_btc(),
        side=OrderSide.BUY,
        fill_quantity=qty,
        fill_price=avg_price,
        is_last_fill=True,
    )
    msgbus.publish(f"events.fills.{_btc()}", fill)
    pos = pf.position(_btc())
    assert pos is not None and pos.quantity == qty and pos.avg_open_price == avg_price
    return pf


def _guard_with_capture(
    pf: Portfolio,
    msgbus: MessageBus,
    **thresholds: float | None,
) -> tuple[PositionGuard, list[SubmitOrderCommand]]:
    """建 guard + 注册 EXECUTION_ENGINE_ENDPOINT capture，返回 (guard, captured cmds)。"""
    captured: list[SubmitOrderCommand] = []
    msgbus.register_endpoint(
        EXECUTION_ENGINE_ENDPOINT,
        lambda cmd: captured.append(cmd),  # type: ignore[arg-type, return-value]
    )
    guard = PositionGuard(msgbus, TestClock(0), pf, **thresholds)
    guard.bind_strategy(_SID)
    return guard, captured


# ─── 硬止损 ───


def test_stop_loss_triggers_full_close() -> None:
    """浮亏穿 -stop_loss_pct → 一笔 SELL 全平，tag=stop_loss，并已提交到 EE。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=2.0, avg_price=100.0)
    guard, captured = _guard_with_capture(pf, msgbus, stop_loss_pct=0.20)

    # mark=79 → -21% 穿过 -20%
    orders = guard.evaluate(_bar(79.0))

    assert len(orders) == 1
    order = orders[0]
    assert order.side == OrderSide.SELL
    assert order.type == OrderType.MARKET
    assert order.quantity == 2.0  # 全平
    assert order.tag == "stop_loss"
    assert order.tag in PROTECTIVE_EXIT_TAGS
    # 已提交到 ExecutionEngine endpoint（绕过 RiskEngine）
    assert len(captured) == 1
    assert captured[0].order.client_order_id == order.client_order_id
    assert captured[0].strategy_id == _SID


def test_stop_loss_not_triggered_above_threshold() -> None:
    """浮亏未穿阈值 → 不触发。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, captured = _guard_with_capture(pf, msgbus, stop_loss_pct=0.20)

    # mark=85 → -15%，未穿 -20%
    assert guard.evaluate(_bar(85.0)) == []
    assert captured == []


# ─── 止盈 ───


def test_take_profit_triggers() -> None:
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(pf, msgbus, take_profit_pct=0.30)

    # mark=131 → +31% 穿过 +30%
    orders = guard.evaluate(_bar(131.0))
    assert len(orders) == 1
    assert orders[0].tag == "take_profit"


def test_take_profit_disabled_by_default_none() -> None:
    """take_profit_pct=None → 上行不平仓。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(pf, msgbus, stop_loss_pct=0.20)

    assert guard.evaluate(_bar(200.0)) == []  # +100% 也不平（止盈关）


# ─── 移动止损 ───


def test_trailing_triggers_after_peak() -> None:
    """先涨到峰值，再自峰值回撤 >= trailing_stop_pct → 触发 trailing_stop_loss。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(pf, msgbus, trailing_stop_pct=0.10)

    # 第一根：mark=130 建立峰值价，自身回撤 0 → 不触发
    assert guard.evaluate(_bar(130.0, ts_ns=1)) == []
    # 第二根：mark=115，自峰值价 130 回撤 (130-115)/130≈11.5% >= 10% → 触发
    orders = guard.evaluate(_bar(115.0, ts_ns=2))
    assert len(orders) == 1
    assert orders[0].tag == "trailing_stop_loss"


def test_trailing_uses_price_drawdown_not_return_pct() -> None:
    """大盈利下 trailing 用「自峰值价格回撤」而非「成本收益率降幅」(CR #88 medium)。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(pf, msgbus, trailing_stop_pct=0.10)

    # 峰值 mark=200（+100%）
    assert guard.evaluate(_bar(200.0, ts_ns=1)) == []
    # mark=185：自峰值价回撤 (200-185)/200=7.5% < 10% → 不触发
    #（旧的成本收益率口径会是 100%-85%=15% >=10% 误触发——本测试钉住新口径）
    assert guard.evaluate(_bar(185.0, ts_ns=2)) == []
    # mark=175：自峰值价回撤 (200-175)/200=12.5% >= 10% → 触发
    orders = guard.evaluate(_bar(175.0, ts_ns=3))
    assert len(orders) == 1
    assert orders[0].tag == "trailing_stop_loss"


def test_trailing_inactive_when_never_profitable() -> None:
    """移动止损仅在仓位进入盈利区后生效；从未盈利(峰值价≤成本)不触发(CR #88 medium)。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(pf, msgbus, trailing_stop_pct=0.10)

    # 开仓即走低：mark 95（峰值价=95<成本100），再跌到 80
    assert guard.evaluate(_bar(95.0, ts_ns=1)) == []
    # 自峰值价 95 回撤 (95-80)/95≈15.8% >= 10%，但峰值价 95 < 成本 100 → trailing 不生效
    assert guard.evaluate(_bar(80.0, ts_ns=2)) == []


# ─── 工厂 / 关闭 / 边界 ───


def test_from_thresholds_all_none_returns_none() -> None:
    msgbus = MessageBus()
    pf = Portfolio(msgbus, initial_cash=10_000.0)
    guard = PositionGuard.from_thresholds(
        msgbus,
        TestClock(0),
        pf,
        stop_loss_pct=None,
        take_profit_pct=None,
        trailing_stop_pct=None,
    )
    assert guard is None


def test_flat_position_returns_empty_and_clears_peak() -> None:
    """无持仓 → 返空；峰值被清除。"""
    msgbus = MessageBus()
    pf = Portfolio(msgbus, initial_cash=10_000.0)  # 空仓
    guard, _ = _guard_with_capture(pf, msgbus, stop_loss_pct=0.20)
    assert guard.evaluate(_bar(50.0)) == []


def test_short_position_is_noop() -> None:
    """spot 当前只管 long；short 持仓 no-op（留待合约阶段）。"""
    msgbus = MessageBus()
    pf = Portfolio(msgbus, initial_cash=1_000_000.0, fee_rate=0.0)
    # 直接造一个 short 仓（SELL 开空）—— 仅为测 guard 对负 quantity 的 no-op
    fill = OrderFilled(
        client_order_id=ClientOrderId("s"),
        strategy_id=_SID,
        ts_event=0,
        ts_init=0,
        instrument_id=_btc(),
        side=OrderSide.SELL,
        fill_quantity=1.0,
        fill_price=100.0,
        is_last_fill=True,
    )
    msgbus.publish(f"events.fills.{_btc()}", fill)
    guard, captured = _guard_with_capture(pf, msgbus, stop_loss_pct=0.20)
    # 价格暴涨（对 short 是大亏）也不动 —— short 由后续合约阶段处理
    assert guard.evaluate(_bar(200.0)) == []
    assert captured == []


def test_no_strategy_bound_returns_empty() -> None:
    """未 bind_strategy → 不提交（防裸提交无主单）。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    captured: list[SubmitOrderCommand] = []
    msgbus.register_endpoint(
        EXECUTION_ENGINE_ENDPOINT,
        lambda cmd: captured.append(cmd),  # type: ignore[arg-type, return-value]
    )
    guard = PositionGuard(msgbus, TestClock(0), pf, stop_loss_pct=0.20)
    # 没 bind
    assert guard.evaluate(_bar(50.0)) == []
    assert captured == []


def test_bind_strategy_rejects_second_strategy() -> None:
    """单策略约束：绑第二个不同 strategy_id → RuntimeError(CR #88)。"""
    import pytest

    msgbus = MessageBus()
    pf = Portfolio(msgbus, initial_cash=10_000.0)
    guard = PositionGuard(msgbus, TestClock(0), pf, stop_loss_pct=0.20)
    guard.bind_strategy(StrategyId("A"))
    guard.bind_strategy(StrategyId("A"))  # 同 id 重复绑定 ok（幂等）
    with pytest.raises(RuntimeError, match="只支持单策略"):
        guard.bind_strategy(StrategyId("B"))
