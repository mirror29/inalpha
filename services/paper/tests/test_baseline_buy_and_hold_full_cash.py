"""D-9 修复回归测试：baseline qty preflight + SMA position_pct + initial_cash 自动注入。

防止「简单持有 -98%」回归（详见 plan: image-1-btc-staged-muffin.md）。

覆盖：
- baseline buy_and_hold 用预算 qty 后 cash 不再变负、最大回撤 ≤ 100%
- SMACrossStrategy 传 position_pct + initial_cash 后按本金比例下单
- runner.run_engine_in_subprocess 自动注入 initial_cash，仅对接受字段的 strategy 生效
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar


def _build_flat_bars(
    n: int = 50,
    open_price: float = 40_000.0,
    close_price: float = 40_000.0,
) -> tuple[InstrumentId, list[Bar]]:
    """线性匀速变价 K 线，便于精确算 baseline 期望收益。"""
    instrument_id = InstrumentId(symbol="BTC/USDT", venue="binance")
    base_ts = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = int(timedelta(hours=1).total_seconds() * 1_000_000_000)
    bars: list[Bar] = []
    for i in range(n):
        p = open_price + (close_price - open_price) * i / max(n - 1, 1)
        bars.append(
            Bar(
                instrument_id=instrument_id,
                timeframe="1h",
                open=p,
                high=p,
                low=p,
                close=p,
                volume=1.0,
                ts_event=base_ts + i * interval_ns,
                ts_init=base_ts + i * interval_ns,
            )
        )
    return instrument_id, bars


def _build_sine_bars(n: int = 50) -> tuple[InstrumentId, list[Bar]]:
    """正弦波 K 线，便于触发 SMA 交叉信号；价格区间 [90, 110]。"""
    instrument_id = InstrumentId(symbol="BTC/USDT", venue="binance")
    base_ts = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    interval_ns = int(timedelta(hours=1).total_seconds() * 1_000_000_000)
    bars: list[Bar] = []
    for i in range(n):
        price = 100.0 + 10.0 * math.sin(i * 0.4)
        bars.append(
            Bar(
                instrument_id=instrument_id,
                timeframe="1h",
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=1.0,
                ts_event=base_ts + i * interval_ns,
                ts_init=base_ts + i * interval_ns,
            )
        )
    return instrument_id, bars


def test_baseline_buy_and_hold_full_cash_no_negative_drawdown() -> None:
    """复现 image-1 bug 的反向用例：baseline qty 用 initial_cash/first_open
    预算后，10k 账户买 BTC 不应再让 cash 变负、最大回撤不超 100%。

    旧 bug：runner 硬编码 trade_size=0.5 → 买 0.5 BTC × $40k = $20k > $10k cash →
    cash 变 -$10k → equity 算出 -98%。修复后 qty ≈ 0.2493 BTC，全程 cash 正常。
    """
    initial_cash = 10_000.0
    fee_rate = 0.001
    open_price = 40_000.0
    close_price = 20_000.0  # Q1 2026 极端假设：BTC 跌 50%
    instrument_id, bars = _build_flat_bars(
        n=90, open_price=open_price, close_price=close_price
    )

    first_open = bars[0].open
    baseline_qty = initial_cash / first_open / (1.0 + fee_rate)

    from inalpha_paper.strategies import get_strategy_class

    engine = BacktestEngine(initial_cash=initial_cash, fee_rate=fee_rate)
    cls = get_strategy_class("buy_and_hold")
    strategy = cls(  # type: ignore[call-arg]
        name="baseline-bh",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=instrument_id,
        timeframe="1h",
        trade_size=baseline_qty,
    )
    engine.add_strategy(strategy)
    report = engine.run(bars)

    # 1. cash 始终非负 → equity 也是正的
    assert engine.portfolio.cash >= -1.0, (
        f"cash 不应变成大负数，但拿到 {engine.portfolio.cash:.2f}"
    )
    # 2. 最大回撤 ≤ 100%（旧 bug 拿过 116.79%）
    assert 0.0 <= report.max_drawdown_pct <= 100.0, (
        f"max_drawdown_pct 应在 [0, 100]，拿到 {report.max_drawdown_pct:.2f}"
    )
    # 3. 总收益 ≈ (close/open - 1) * 100，容差 1.5pp（手续费 + qty 取整）
    expected_return_pct = (close_price / open_price - 1.0) * 100.0
    assert abs(report.total_return_pct - expected_return_pct) < 1.5, (
        f"total_return_pct={report.total_return_pct:.2f} 应接近 "
        f"{expected_return_pct:.2f}（BTC 跌 50%）"
    )


def test_sma_cross_position_pct_uses_full_cash() -> None:
    """SMACrossStrategy 传 position_pct=1.0 + initial_cash=10k 时，首笔 BUY 订单
    quantity 应 ≈ initial_cash / bar.open / (1 + fee_buffer)，不再走 trade_size 绝对量。
    """
    from inalpha_paper.kernel.clock import TestClock
    from inalpha_paper.kernel.msgbus import MessageBus
    from inalpha_paper.model.commands import SubmitOrderCommand
    from inalpha_paper.strategies.sma_cross import SMACrossStrategy
    from inalpha_paper.strategy.base import RISK_ENGINE_ENDPOINT

    clock = TestClock(0)
    bus = MessageBus()
    captured: list[SubmitOrderCommand] = []
    bus.register_endpoint(
        RISK_ENGINE_ENDPOINT,
        lambda m: captured.append(m) if isinstance(m, SubmitOrderCommand) else None,
    )
    instrument_id, bars = _build_sine_bars(n=60)
    strat = SMACrossStrategy(
        name="sma-pct",
        clock=clock,
        msgbus=bus,
        instrument_id=instrument_id,
        timeframe="1h",
        fast_period=3,
        slow_period=5,
        position_pct=1.0,
        initial_cash=10_000.0,
    )
    strat.on_start()
    for bar in bars:
        bus.publish(
            f"data.bars.{bar.instrument_id.venue}."
            f"{bar.instrument_id.symbol}.{bar.timeframe}",
            bar,
        )

    assert captured, "正弦波应触发至少一次 SMA 上穿信号"
    first_buy = next(c for c in captured if c.order.side.name == "BUY")
    qty = first_buy.order.quantity
    implied_open = 10_000.0 / qty / (1.0 + 0.001)
    assert 90.0 <= implied_open <= 110.0, (
        f"qty={qty} 反推 open={implied_open:.2f} 不在 [90, 110]"
    )
    # 老语义 trade_size=0.01 会让 qty=0.01；新满仓 qty 远大于此
    assert qty > 1.0, f"position_pct 路径下 qty 应远大于 trade_size=0.01，拿到 {qty}"


def test_run_engine_injects_initial_cash_only_for_accepting_strategies() -> None:
    """runner.run_engine_in_subprocess 用 inspect.signature 检测 strategy ``__init__``
    接受 ``initial_cash`` 时才注入，保持 mean_reversion 等未升级 strategy 兼容。
    """
    from inalpha_paper.runner import run_engine_in_subprocess

    instrument_id, bars = _build_flat_bars(n=40, open_price=100.0, close_price=110.0)

    # SMACrossStrategy 接受 initial_cash → 走 position_pct 路径
    report_sma = run_engine_in_subprocess(
        bars=bars,
        instrument_id=instrument_id,
        timeframe="1h",
        strategy_id="sma_cross",
        params={"fast_period": 3, "slow_period": 5, "position_pct": 1.0},
        initial_cash=10_000.0,
        fee_rate=0.001,
    )
    assert report_sma.num_bars_processed == 40

    # MeanReversion 不接受 initial_cash → 不注入，仍能跑通（不抛 unexpected kwarg）
    report_mr = run_engine_in_subprocess(
        bars=bars,
        instrument_id=instrument_id,
        timeframe="1h",
        strategy_id="mean_reversion",
        params={"period": 10, "std_mult": 2.0, "trade_size": 0.01},
        initial_cash=10_000.0,
        fee_rate=0.001,
    )
    assert report_mr.num_bars_processed == 40
