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
from inalpha_paper.model.orders import (
    PROTECTIVE_EXIT_TAGS,
    Order,
    OrderSide,
    OrderType,
    is_protective_order,
)

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


def _ohlc(o: float, h: float, low: float, c: float, ts_ns: int) -> Bar:
    """显式 OHLC bar（chandelier 测试用：ATR 需要 high/low/prev_close 算 TR）。"""
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=o,
        high=h,
        low=low,
        close=c,
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


# ─── chandelier（吊灯）ATR 移动止损（ADR-0052 增补 A）───


def test_chandelier_triggers_on_atr_drop() -> None:
    """涨出最高价、ATR 种子就绪后，收盘跌穿 最高价 − mult×ATR → 触发 trailing_stop_loss。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(
        msgbus=msgbus, pf=pf, chandelier_atr_mult=1.0, chandelier_atr_period=2
    )

    # bar1：建最高价 110，prev_close 未就绪无 TR、atr None → 不触发
    assert guard.evaluate(_ohlc(100.0, 110.0, 99.0, 108.0, 1)) == []
    # bar2：最高价升到 120，TR2 入种子（tr_count=1<2）、atr 仍 None → 不触发
    assert guard.evaluate(_ohlc(108.0, 120.0, 107.0, 118.0, 2)) == []
    # bar3：最高价 124，TR3 让 atr 种子就绪（atr=10.5）；止损位=124−10.5=113.5，mark=120>113.5 不触发
    assert guard.evaluate(_ohlc(118.0, 124.0, 116.0, 120.0, 3)) == []
    # bar4：atr≈10.75，止损位=124−10.75≈113.25，mark=112≤113.25 → 触发，复用 trailing_stop_loss tag
    orders = guard.evaluate(_ohlc(120.0, 121.0, 110.0, 112.0, 4))
    assert len(orders) == 1
    assert orders[0].tag == "trailing_stop_loss"
    assert orders[0].tag in PROTECTIVE_EXIT_TAGS
    assert orders[0].quantity == 1.0  # 全平


def test_chandelier_no_trigger_before_atr_seeded() -> None:
    """ATR 种子未就绪（开仓后不足 period 根）期间 chandelier 静默，即便大跌也不触发。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(
        msgbus=msgbus, pf=pf, chandelier_atr_mult=1.0, chandelier_atr_period=10
    )

    assert guard.evaluate(_ohlc(100.0, 130.0, 99.0, 128.0, 1)) == []  # 建高点，无 TR
    # 暴跌到 80（自高点 −38%），但 period=10 atr 远未就绪 → chandelier 不触发
    assert guard.evaluate(_ohlc(128.0, 129.0, 80.0, 80.0, 2)) == []


def test_chandelier_inactive_when_never_profitable() -> None:
    """chandelier 仅在「曾进盈利区」（最高价 > 成本）后生效；从未盈利不触发。"""
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(
        msgbus=msgbus, pf=pf, chandelier_atr_mult=1.0, chandelier_atr_period=2
    )

    # 价格全程在成本 100 之下（最高价 ≤ 99）：atr 会就绪但 highest_high < avg → 不生效
    assert guard.evaluate(_ohlc(100.0, 99.0, 95.0, 98.0, 1)) == []
    assert guard.evaluate(_ohlc(98.0, 97.0, 90.0, 92.0, 2)) == []
    assert guard.evaluate(_ohlc(92.0, 93.0, 80.0, 82.0, 3)) == []


def test_from_thresholds_chandelier_only_builds_guard() -> None:
    """仅配 chandelier（其余 None）→ from_thresholds 仍建出 guard（不退化为 None）。"""
    msgbus = MessageBus()
    pf = Portfolio(msgbus, initial_cash=10_000.0)
    guard = PositionGuard.from_thresholds(
        msgbus,
        TestClock(0),
        pf,
        stop_loss_pct=None,
        take_profit_pct=None,
        trailing_stop_pct=None,
        chandelier_atr_mult=2.0,
    )
    assert guard is not None


def test_chandelier_param_validation() -> None:
    """chandelier_atr_mult ≤ 0 / period < 2 → ValueError。"""
    import pytest

    msgbus = MessageBus()
    pf = Portfolio(msgbus, initial_cash=10_000.0)
    with pytest.raises(ValueError, match="chandelier_atr_mult"):
        PositionGuard(msgbus, TestClock(0), pf, chandelier_atr_mult=0.0)
    with pytest.raises(ValueError, match="chandelier_atr_period"):
        PositionGuard(
            msgbus, TestClock(0), pf, chandelier_atr_mult=2.0, chandelier_atr_period=1
        )


# ─── pending-exit 去重（防 live 撮合延迟重复触发，#91）───


def test_no_duplicate_exit_while_pending_fill() -> None:
    """已提交保护性出场、持仓尚未平（撮合下一根才发生）→ 再次 evaluate 不重复下单 (#91)。

    捕获 endpoint 不真撮合，故 pf 持仓在 evaluate 之间保持开仓——模拟 live 撮合延迟。
    无去重时第二次会再发一笔；有去重则 captured 始终 1。
    """
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, captured = _guard_with_capture(pf, msgbus, stop_loss_pct=0.20)

    orders1 = guard.evaluate(_bar(79.0, ts_ns=1))  # -21% 穿阈 → 出 1 单 + 标 pending
    assert len(orders1) == 1
    assert len(captured) == 1

    orders2 = guard.evaluate(_bar(78.0, ts_ns=2))  # 持仓未平、仍穿阈 → 不重复
    assert orders2 == []
    assert len(captured) == 1  # 没多发第二笔


def test_cancel_pending_exit_rearms_guard_after_reject() -> None:
    """出场单被拒（live 路由失败）→ cancel_pending_exit 解除去重 → 下一根重新触发可再发。

    防 #94 死锁：否则出场没成交、持仓不 flat，guard 永久跳过该 inst → 灾难止损静默失效。
    """
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, captured = _guard_with_capture(pf, msgbus, stop_loss_pct=0.20)

    guard.evaluate(_bar(79.0, ts_ns=1))  # 出 1 单 + 标 pending
    assert len(captured) == 1
    guard.evaluate(_bar(78.0, ts_ns=2))  # pending → 跳过
    assert len(captured) == 1
    guard.cancel_pending_exit(_btc())  # 出场被拒 → 解除去重
    guard.evaluate(_bar(77.0, ts_ns=3))  # 重新评估、仍穿阈 → 再发（不再死锁）
    assert len(captured) == 2


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


def _order(tag: str | None, coid: str, side: OrderSide = OrderSide.SELL) -> Order:
    return Order(
        client_order_id=ClientOrderId(coid),
        instrument_id=_btc(),
        side=side,
        type=OrderType.MARKET,
        quantity=1.0,
        tag=tag,
    )


def test_is_protective_order_requires_sell_tag_and_guard_prefix() -> None:
    """CR #88 major 回归：风控豁免三因子判定（side=SELL + tag + guard 前缀），缺一即仿冒。

    - guard 真出场单（SELL + tag ∈ 保护集 + 'guard-' 前缀）→ True
    - 仅改 tag（普通 client_order_id）→ False
    - guard 前缀但非保护性 tag → False
    - **BUY 单即便 tag + 前缀都伪造 → False**（guard 永不做 BUY；挡借豁免下超大开仓单）
    """
    # guard 真单
    assert is_protective_order(_order("stop_loss", "guard-BTC/USDT-abc123")) is True
    # 仅改 tag
    assert is_protective_order(_order("stop_loss", "sma-BTC/USDT-deadbeef")) is False
    # guard 前缀但 tag 不在保护集
    assert is_protective_order(_order("signal", "guard-BTC/USDT-xyz")) is False
    # 双因子全伪造但 side=BUY → 仍 False（side 守门挡掉借豁免开超大单）
    forged_buy = _order("stop_loss", "guard-BTC/USDT-evil", side=OrderSide.BUY)
    assert is_protective_order(forged_buy) is False
    # guard 自己产的出场单恒满足（与生产构造一致）
    msgbus = MessageBus()
    pf = _long_portfolio(msgbus, qty=1.0, avg_price=100.0)
    guard, _ = _guard_with_capture(pf, msgbus, stop_loss_pct=0.20)
    orders = guard.evaluate(_bar(70.0))
    assert len(orders) == 1
    assert is_protective_order(orders[0]) is True
