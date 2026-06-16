"""端到端 backtest 测试 —— SMA cross 跑完整闭环。

测试目标（D-5 验收标准）：
**用合成数据跑通 K 线 → 策略 → 信号 → 撮合 → 仓位变化 → PnL 报告整条路径**。
"""
from __future__ import annotations

import math

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.engine.live_session import LiveEngineSession
from inalpha_paper.engine.position_guard import PROTECTIVE_EXIT_TAGS
from inalpha_paper.engine.report import BacktestReport
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.strategies.buy_and_hold import BuyAndHoldStrategy
from inalpha_paper.strategies.sma_cross import SMACrossStrategy


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _bar(open: float, high: float, low: float, close: float, ts_ns: int) -> Bar:
    return Bar(
        instrument_id=_btc(),
        timeframe="1h",
        open=open,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def _gen_bars(prices: list[float], step_ns: int = 3600 * 1_000_000_000) -> list[Bar]:
    """从一串 close 价格生成 bars。open=high=low=close=p（简化撮合）。"""
    return [
        _bar(open=p, high=p, low=p, close=p, ts_ns=(i + 1) * step_ns)
        for i, p in enumerate(prices)
    ]


# ─── 端到端：sinusoidal 价格 + SMA cross ───


def test_backtest_sma_cross_on_oscillating_prices() -> None:
    """振荡价格 + SMA cross → 触发多次交易，验证完整链路连通。

    用 sin 波合成价格，让 SMA cross 必然交叉多次（每次 ~波长/2）。
    """
    # 价格 = 100 + 10 * sin(2*pi*i/20)，20 周期，跑 5 个周期 = 100 bars
    prices = [100 + 10 * math.sin(2 * math.pi * i / 20) for i in range(100)]
    bars = _gen_bars(prices)

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    strat = SMACrossStrategy(
        name="sma",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        fast_period=5,
        slow_period=15,
        trade_size=0.05,
    )
    engine.add_strategy(strat)

    report = engine.run(bars)

    # 报告字段就位
    assert isinstance(report, BacktestReport)
    assert report.num_bars_processed == 100
    assert report.period_start is not None
    assert report.period_end is not None
    # SMA cross 在振荡市必然触发若干次（保守下界 2）
    assert report.num_trades >= 2
    # 振荡价格 + 手续费，策略大概率亏一点点；但 equity 应该接近初始
    assert 9_000 <= report.final_equity <= 11_000
    # 至少有一次 BUY 信号被发出
    assert strat.signal_count >= 2

    # 绩效指标字段（D-7+ 新加）
    assert len(report.equity_curve) == report.num_bars_processed
    # 每个点都是 (ts_ns, equity)
    assert all(isinstance(p[0], int) and isinstance(p[1], float) for p in report.equity_curve)
    # 振荡市最大回撤应该 > 0（有交易必然有回撤）
    assert report.max_drawdown_pct > 0.0
    # Sharpe 应有值（≥2 笔交易 + 100 bar，必然非平稳）
    assert report.sharpe is not None
    # Sortino 同理
    assert report.sortino is not None
    # 振荡市 → 至少有完整 round-trip，胜率有值
    assert report.win_rate is not None
    assert 0.0 <= report.win_rate <= 100.0

    # ADR-0027 防过拟合：Sharpe 有定义 + 未穿仓 → Bootstrap Sharpe CI 已算出
    assert report.sharpe_ci_includes_zero is not None
    assert report.sharpe_ci_lower is not None
    assert report.sharpe_ci_upper is not None
    # CI 下界 ≤ 上界；includes_zero 真值与"下界<0<上界"自洽
    assert report.sharpe_ci_lower <= report.sharpe_ci_upper
    spans_zero = report.sharpe_ci_lower <= 0.0 <= report.sharpe_ci_upper
    assert report.sharpe_ci_includes_zero == spans_zero


# ─── 逐笔成交记录（含每笔盈亏） ───


def test_backtest_records_per_fill_trades() -> None:
    """振荡市 → report.fills 收齐每笔成交，含正确 intent 与每笔实现盈亏。

    验收（D-11+ 详情页「回测成交」）：
    - 每笔 fill 一条记录，条数 == num_trades
    - intent 取值合法，且首笔（空仓 BUY）= open_long
    - 平仓笔 realized_pnl 增量之和 == round-trip closed_trade_pnls 之和（开仓笔=0）
    """
    prices = [100 + 10 * math.sin(2 * math.pi * i / 20) for i in range(100)]
    bars = _gen_bars(prices)

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    strat = SMACrossStrategy(
        name="sma",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        fast_period=5,
        slow_period=15,
        trade_size=0.05,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    assert report.num_trades >= 2
    # 每笔成交一条记录
    assert len(report.fills) == report.num_trades
    # 字段合理
    for f in report.fills:
        assert f.side in ("BUY", "SELL")
        assert f.intent in ("open_long", "open_short", "close")
        assert f.quantity > 0
        assert f.fill_price > 0
        assert f.fee >= 0
        assert f.order_type == "MARKET"
    # 现货 long-only：首笔必是空仓买入 = 开多
    assert report.fills[0].side == "BUY"
    assert report.fills[0].intent == "open_long"
    # 每笔实现盈亏之和 == round-trip 盈亏之和（开仓笔贡献 0；in-process 跑，portfolio 即最终态）
    assert math.isclose(
        sum(f.realized_pnl for f in report.fills),
        sum(engine.portfolio.closed_trade_pnls),
        rel_tol=1e-9,
        abs_tol=1e-6,
    )


# ─── 上涨趋势：买入持有 ───


def test_backtest_uptrend_profitable() -> None:
    """单调上涨趋势 → 金叉买入后持有 → 报告应该是正收益。"""
    # 价格先平稳再上涨：让快线必然上穿慢线
    prices = [100.0] * 20 + [100.0 + i * 0.5 for i in range(1, 30)]
    bars = _gen_bars(prices)

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.0)  # 零费率，专测策略
    strat = SMACrossStrategy(
        name="up",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        fast_period=3,
        slow_period=8,
        trade_size=1.0,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    # 单调上涨 + 金叉买入 + 持有到结束 → 正收益
    assert report.num_trades >= 1
    assert report.total_return_pct > 0


# ─── 下跌趋势：不入场或快速出场 ───


def test_backtest_downtrend_no_buy_signals() -> None:
    """单调下跌趋势：快线在慢线下方，永远不会金叉 → 不下单。"""
    prices = [100.0 - i * 0.5 for i in range(60)]
    bars = _gen_bars(prices)

    engine = BacktestEngine(initial_cash=10_000.0)
    strat = SMACrossStrategy(
        name="down",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        fast_period=3,
        slow_period=8,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    assert report.num_trades == 0
    assert report.final_equity == 10_000.0
    # 没交易 → 没 round-trip → win_rate=None
    assert report.win_rate is None
    # equity 全程平稳 → 0 回撤
    assert report.max_drawdown_pct == 0.0
    # equity 全程平稳 → Sharpe=None（std=0）
    assert report.sharpe is None
    # ADR-0027：Sharpe 未定义 → 不算 Bootstrap CI（None，不污染响应）
    assert report.sharpe_ci_includes_zero is None
    assert report.sharpe_ci_lower is None
    assert report.sharpe_ci_upper is None


# ─── 空 bars 报错 ───


def test_empty_bars_raises() -> None:
    engine = BacktestEngine()
    import pytest

    with pytest.raises(ValueError, match="at least one bar"):
        engine.run([])


# ─── ADR-0052 · 框架级持仓保护止损（回测 + live session 一致性） ───

# buy-and-hold 在 bar0 买入、价格平稳后跌至 -30%。
# 回测：bar0 下单 → bar1 成交建仓 @100 → bar4 mark=70 触发 → bar5 成交平仓。
# 末段价格恒 70，使两套引擎的"触发判定"落在同一根（bar4 mark=70 穿 -20%）。
_CRASH_PRICES = [100.0, 100.0, 100.0, 100.0, 70.0, 70.0, 70.0]


def _drive_live_with_guard(
    bars: list[Bar], *, stop_loss_pct: float | None
) -> tuple[list[str], float]:
    """逐根喂 live session 并即时回灌成交（模拟 runner 同 bar 撮合）。

    返回 ``(protective_exit_tags, final_signed_qty)``：所有保护性出场的 tag 列表
    + 末态持仓带符号数量（0 = 已平）。
    """
    session = LiveEngineSession(
        strategy_cls=BuyAndHoldStrategy,
        instrument_id=_btc(),
        timeframe="1h",
        params={},
        initial_cash=10_000.0,
        fee_rate=0.0,
        protective_stop_loss_pct=stop_loss_pct,
    )
    protective_tags: list[str] = []
    for bar in bars:
        orders = session.feed_bar(bar)
        session.take_unsupported_orders()
        for order, sid in orders:
            if order.tag in PROTECTIVE_EXIT_TAGS:
                protective_tags.append(order.tag)
            # runner 在同一 bar 以 ref_price=bar.close 撮合并回灌成交
            session.confirm_fill(
                order=order,
                strategy_id=sid,
                fill_qty=order.quantity,
                fill_price=bar.close,
                ts_event=bar.ts_event,
            )
    pos = session.portfolio.position(_btc())
    return protective_tags, (pos.quantity if pos is not None else 0.0)


def test_backtest_protective_stop_loss_caps_position() -> None:
    """回测：单仓浮亏穿 -20% → 框架兜底平仓（tag=stop_loss），末态空仓。"""
    bars = _gen_bars(_CRASH_PRICES)
    engine = BacktestEngine(
        initial_cash=10_000.0, fee_rate=0.0, protective_stop_loss_pct=0.20
    )
    strat = BuyAndHoldStrategy(
        name="bh",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        trade_size=1.0,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    # 框架兜底触发恰好一次
    assert report.protective_exits == 1
    stop_fills = [f for f in report.fills if f.tag == "stop_loss"]
    assert len(stop_fills) == 1
    assert stop_fills[0].side == "SELL"
    # 兜底已平仓 → 末态无持仓
    pos = report.positions.get(_btc())
    assert pos is None or pos.is_flat


def test_backtest_without_guard_holds_through_crash() -> None:
    """无 guard（阈值 None）：同样的崩盘里不平仓，末态仍持有 —— 对照组。"""
    bars = _gen_bars(_CRASH_PRICES)
    engine = BacktestEngine(
        initial_cash=10_000.0, fee_rate=0.0, protective_stop_loss_pct=None
    )
    strat = BuyAndHoldStrategy(
        name="bh",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        trade_size=1.0,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    assert report.protective_exits == 0
    # 没兜底 → 一路持有到结束
    pos = report.positions.get(_btc())
    assert pos is not None and not pos.is_flat


def test_backtest_guard_and_strategy_double_sell_same_bar_no_corruption() -> None:
    """CR Medium：guard 硬止损 + 策略自主出场同 bar 双 SELL → exchange can_afford_sell
    拒掉多余那笔（INSUFFICIENT_POSITION 拒单日志），但末态干净（空仓 / 不穿仓 / 不负仓）。

    骨架策略（momentum_trend / mean_reversion）都有自主出场，用骨架时这是高频路径。
    """
    from uuid import uuid4

    from inalpha_paper.kernel.identifiers import ClientOrderId
    from inalpha_paper.model.orders import Order, OrderSide, OrderType
    from inalpha_paper.strategy.base import Strategy

    class _BuyThenExitOnDrop(Strategy):
        def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h", trade_size=1.0):  # type: ignore[no-untyped-def]
            super().__init__(name, clock, msgbus)
            self._iid = instrument_id
            self._tf = timeframe
            self._sz = trade_size
            self._is_long = False
            self._open_qty = 0.0

        def on_start(self) -> None:
            self.subscribe_bars(self._iid, self._tf)

        def on_bar(self, bar: Bar) -> None:
            if bar.instrument_id != self._iid:
                return
            if not self._is_long and bar.close >= 100.0:  # 首段建仓
                self._submit(OrderSide.BUY, self._sz)
            elif self._is_long and bar.close <= 75.0:  # 崩盘自主出场（与 guard 同 bar）
                self._submit(OrderSide.SELL, self._open_qty)

        def on_position_opened(self, event) -> None:  # type: ignore[no-untyped-def]
            self._is_long = event.quantity > 0
            self._open_qty = abs(event.quantity)

        def on_position_closed(self, event) -> None:  # type: ignore[no-untyped-def]
            self._is_long = False
            self._open_qty = 0.0

        def _submit(self, side: int, qty: float) -> None:
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId("dbl-" + uuid4().hex[:8]),
                    instrument_id=self._iid,
                    side=side,
                    type=OrderType.MARKET,
                    quantity=qty,
                )
            )

    bars = _gen_bars(_CRASH_PRICES)
    engine = BacktestEngine(
        initial_cash=10_000.0, fee_rate=0.0, protective_stop_loss_pct=0.20
    )
    strat = _BuyThenExitOnDrop(
        name="dbl",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        trade_size=1.0,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    # 核心不变量：双 SELL 不污染 —— 末态空仓、未穿仓、无负持仓
    assert not report.blew_up
    pos = report.positions.get(_btc())
    assert pos is None or pos.is_flat
    if pos is not None:
        assert pos.quantity >= 0  # 多余 SELL 被守门拒，不会卖出负仓


def test_live_session_protective_stop_matches_backtest() -> None:
    """live session 走 feed_bar 路径同样触发框架兜底，与回测同口径（tag=stop_loss、平仓）。

    一致性核心：同一 PositionGuard 组件、同一阈值、挂两套引擎"update_mark 之后"的同一
    逻辑点；末段价格恒 70 使触发判定落在同一根 bar，两边都产出 stop_loss 出场并平仓。
    """
    bars = _gen_bars(_CRASH_PRICES)
    tags, final_qty = _drive_live_with_guard(bars, stop_loss_pct=0.20)
    assert tags == ["stop_loss"]
    assert final_qty == 0.0  # 已平仓

    # 对照：无 guard 时 live 不平仓
    tags_off, final_qty_off = _drive_live_with_guard(bars, stop_loss_pct=None)
    assert tags_off == []
    assert final_qty_off > 0.0  # 仍持有
