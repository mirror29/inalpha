"""端到端 backtest 测试 —— SMA cross 跑完整闭环。

测试目标（D-5 验收标准）：
**用合成数据跑通 K 线 → 策略 → 信号 → 撮合 → 仓位变化 → PnL 报告整条路径**。
"""
from __future__ import annotations

import math
from uuid import uuid4

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.engine.live_session import LiveEngineSession
from inalpha_paper.engine.report import BacktestReport
from inalpha_paper.kernel.clock import Clock
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId
from inalpha_paper.kernel.msgbus import MessageBus
from inalpha_paper.model.data import Bar
from inalpha_paper.model.events import PositionClosed, PositionOpened
from inalpha_paper.model.orders import (
    PROTECTIVE_EXIT_TAGS,
    Order,
    OrderSide,
    OrderType,
)
from inalpha_paper.strategies.buy_and_hold import BuyAndHoldStrategy
from inalpha_paper.strategies.sma_cross import SMACrossStrategy
from inalpha_paper.strategy.base import Strategy


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


def test_backtest_protective_stop_on_last_bar_settles_at_close() -> None:
    """末根 bar 触发兜底也如实成交+计数(按末根 close 兜底平仓),与 live 对齐(CR #88)。

    价格在最后一根才砸穿 -20%：常规 next-bar 撮合没有下一根,会漏计/显示未平；
    收尾 flush_protective_at_close 按末根 close 兜底平仓修正。
    """
    # 买入持有到末根(第5根 idx=4)才跌到 -30%
    bars = _gen_bars([100.0, 100.0, 100.0, 100.0, 70.0])
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

    # 末根触发也计入、也平仓（修复前是 0 / 持有）
    assert report.protective_exits == 1
    stop_fills = [f for f in report.fills if f.tag == "stop_loss"]
    assert len(stop_fills) == 1
    assert stop_fills[0].fill_price == 70.0  # 按末根 close 兜底成交
    pos = report.positions.get(_btc())
    assert pos is None or pos.is_flat


def test_guard_enabled_rejects_second_strategy() -> None:
    """启用 guard 时挂第二个策略 → RuntimeError(单策略约束,CR #88)。"""
    import pytest

    engine = BacktestEngine(
        initial_cash=10_000.0, fee_rate=0.0, protective_stop_loss_pct=0.20
    )
    engine.add_strategy(
        BuyAndHoldStrategy(
            name="a", clock=engine.clock, msgbus=engine.msgbus,
            instrument_id=_btc(), timeframe="1h", trade_size=1.0,
        )
    )
    with pytest.raises(RuntimeError, match="只支持单策略"):
        engine.add_strategy(
            BuyAndHoldStrategy(
                name="b", clock=engine.clock, msgbus=engine.msgbus,
                instrument_id=_btc(), timeframe="1h", trade_size=1.0,
            )
        )


def test_no_guard_allows_multiple_strategies() -> None:
    """未启用 guard(阈值全 None)时多策略仍可挂(不破坏既有能力)。"""
    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.0)
    engine.add_strategy(
        BuyAndHoldStrategy(
            name="a", clock=engine.clock, msgbus=engine.msgbus,
            instrument_id=_btc(), timeframe="1h", trade_size=1.0,
        )
    )
    # 第二个策略不报错
    engine.add_strategy(
        BuyAndHoldStrategy(
            name="b", clock=engine.clock, msgbus=engine.msgbus,
            instrument_id=_btc(), timeframe="1h", trade_size=1.0,
        )
    )


class _StrategyStopLossStrat(Strategy):
    """测试用：bar1 买入，bar3 用 tag='stop_loss' 卖出（普通 client_order_id，非 guard）。"""

    def __init__(
        self,
        name: str,
        clock: Clock,
        msgbus: MessageBus,
        instrument_id: InstrumentId,
        timeframe: str = "1h",
        trade_size: float = 1.0,
        **_: object,
    ) -> None:
        super().__init__(name, clock, msgbus)
        self._iid = instrument_id
        self._tf = timeframe
        self._size = trade_size
        self._n = 0
        self._is_long = False
        self._open_qty = 0.0

    def on_start(self) -> None:
        self.subscribe_bars(self._iid, self._tf)

    def on_bar(self, bar: Bar) -> None:
        self._n += 1
        if self._n == 1:
            self.submit_order(Order(
                client_order_id=ClientOrderId(f"mystrat-{uuid4().hex[:8]}"),
                instrument_id=self._iid, side=OrderSide.BUY,
                type=OrderType.MARKET, quantity=self._size,
            ))
        elif self._n == 3 and self._is_long:
            self.submit_order(Order(
                client_order_id=ClientOrderId(f"mystrat-{uuid4().hex[:8]}"),
                instrument_id=self._iid, side=OrderSide.SELL,
                type=OrderType.MARKET, quantity=self._open_qty, tag="stop_loss",
            ))

    def on_position_opened(self, event: PositionOpened) -> None:
        self._is_long = True
        self._open_qty = abs(event.quantity)

    def on_position_closed(self, event: PositionClosed) -> None:
        self._is_long = False


def test_strategy_own_stop_loss_tag_not_counted_as_guard() -> None:
    """CR #88 major 回归：策略自打 tag='stop_loss' 的平仓不计入 protective_exits。

    protective_exits 只数框架 guard 兜底（is_guard），策略自带止损 tag（client_order_id 非
    'guard-' 前缀）不算——否则 agent 会把策略止损误报成"框架止损"。
    """
    # 价格平稳，guard 不会触发；关掉 guard 也行，这里关掉以纯测策略 tag 计数
    bars = _gen_bars([100.0, 100.0, 100.0, 100.0, 100.0])
    engine = BacktestEngine(
        initial_cash=10_000.0, fee_rate=0.0, protective_stop_loss_pct=None
    )
    engine.add_strategy(
        _StrategyStopLossStrat(
            name="s", clock=engine.clock, msgbus=engine.msgbus,
            instrument_id=_btc(), timeframe="1h", trade_size=1.0,
        )
    )
    report = engine.run(bars)

    # 策略确实平了一笔且打了 stop_loss tag
    stop_fills = [f for f in report.fills if f.tag == "stop_loss"]
    assert len(stop_fills) == 1
    assert stop_fills[0].is_guard is False  # 关键：不是框架 guard
    # 框架 guard 计数为 0（不被策略自带 tag 污染）
    assert report.protective_exits == 0
