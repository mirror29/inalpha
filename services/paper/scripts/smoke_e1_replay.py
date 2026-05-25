"""E1 闭环 · 第 2 步：从 stdin 读 signals，喂 BacktestEngine 跑回测打印 metrics。

链路（接 smoke-e1-extract.ts 的输出）：

    stdin (strategy_v1 JSON) → SignalReplayStrategy → BacktestEngine → BacktestReport

用法（独立跑，测试用）：

    echo '{"version":"strategy_v1","signals":[{"ts":1700010800000,"side":"BUY","qty":0.5}]}' \\
        | uv run python services/paper/scripts/smoke_e1_replay.py

跨语言端到端（推荐入口）：

    bash scripts/smoke-e1-loop.sh

为什么用 stdin 而不是文件：bash pipe 最简单，零临时文件清理，跨平台靠谱。
"""
from __future__ import annotations

import json
import sys

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.strategies.signal_replay import SignalReplayStrategy

_NS_PER_MS = 1_000_000


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _synth_bars() -> list[Bar]:
    """造 10 根 1h bar，价格 100 → 110 平滑上升。

    bar[i].ts_event = 1_700_000_000_000_ms + i * 3600_000_ms（转 ns）
    BUY signal 用 ts=1_700_010_800_000（bar[3]），SELL ts=1_700_025_200_000（bar[7]）。
    """
    prices = [100, 102, 104, 106, 108, 110, 110, 110, 110, 110]
    start_ms = 1_700_000_000_000
    step_ms = 3_600_000
    return [
        Bar(
            instrument_id=_btc(),
            timeframe="1h",
            open=p, high=p, low=p, close=p, volume=1.0,
            ts_event=(start_ms + i * step_ms) * _NS_PER_MS,
            ts_init=(start_ms + i * step_ms) * _NS_PER_MS,
        )
        for i, p in enumerate(prices)
    ]


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print("ERROR: empty stdin; expected strategy_v1 JSON", file=sys.stderr)
        sys.exit(1)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: stdin not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    if payload.get("version") != "strategy_v1":
        print(f"ERROR: expected version=strategy_v1, got {payload.get('version')}", file=sys.stderr)
        sys.exit(1)

    signals = payload.get("signals") or []
    print(f"→ 收到 {len(signals)} 条 signals: {signals}")

    bars = _synth_bars()
    print(f"→ 合成 {len(bars)} 根 1h bar，价格 100→110 平滑上升")

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    strat = SignalReplayStrategy(
        name="signal_replay-BTC/USDT",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        signals=signals,
    )
    engine.add_strategy(strat)

    report = engine.run(bars)

    sharpe = f"{report.sharpe:.2f}" if report.sharpe is not None else "n/a"
    win = f"{report.win_rate:.1f}%" if report.win_rate is not None else "n/a"
    sign = "+" if report.total_return_pct >= 0 else ""

    print()
    print("─" * 60)
    print(f"  E1 BacktestReport · {strat.replayed_count}/{strat.initial_signal_count} signals replayed")
    print("─" * 60)
    print(f"  initial_cash     = {report.initial_cash:.2f}")
    print(f"  final_equity     = {report.final_equity:.2f}")
    print(f"  total_return     = {sign}{report.total_return_pct:.4f}%")
    print(f"  num_trades       = {report.num_trades}")
    print(f"  total_fees       = {report.total_fees:.4f}")
    print(f"  sharpe (1h ann.) = {sharpe}")
    print(f"  max_drawdown     = {report.max_drawdown_pct:.4f}%")
    print(f"  win_rate         = {win}")
    print(f"  equity_curve     = {len(report.equity_curve)} pts")
    print("─" * 60)
    print("✅ E1 闭环端到端：sandbox → strategy_v1 → SignalReplayStrategy → BacktestReport")


if __name__ == "__main__":
    main()
