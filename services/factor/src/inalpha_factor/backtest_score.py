"""P0 · 因子→策略回测闭环：``POST /backtest/score``。

评估一个自定义 DSL 表达式因子后，自动构造最简策略（因子排名 top1 → 次日换仓）
跑 WalkForward 回测，返回 OOS Sharpe / MaxDD / WinRate 作为比 IC 更有说服力的评判标准。
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from .adapters import FactorSpec
from .effectiveness import ic_pvalue, score_factor
from .engine import FactorEngine
from .engine import _eff_to_dict as _engine_eff_to_dict
from .expression import evaluate, parse_expression

logger = logging.getLogger(__name__)

#: timeframe → 秒/bar（同 engine._TF_SECONDS）
_TF_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400, "1wk": 604800,
}


def _tf_seconds(timeframe: str) -> int:
    return _TF_SECONDS.get(timeframe, 3600)


# ── 最简策略：因子排名 top quantile → 次日换仓 ─────────────────────────


def _build_signal_from_factor(
    factor_series: pd.Series,
    close: pd.Series,
    horizon: int,
    top_quantile: float = 0.2,
) -> pd.Series:
    """从因子时序构造交易信号：每根 bar 的 top quantile 标的做多/全仓做空。

    信号值:
    - +1（做多）: 因子值在 top ``top_quantile`` 分位
    - -1（做空）: 因子值在 bottom ``top_quantile`` 分位
    - 0（空仓）: 中间区域

    Args:
        factor_series: 因子值时序（含 NaN warmup 段）。
        close: 收盘价时序（与 factor_series 同索引）。
        horizon: 前瞻收益窗口（决定信号到收益的 bar 数偏移）。
        top_quantile: 头部/尾部比例（默认 0.2 = 20%）。

    Returns:
        信号时序，长度与 factor_series 相同，末尾 ``horizon`` 根为 NaN（无前瞻收益）。
    """
    # 滚动分位阈值
    rank = factor_series.rank(pct=True, na_option="keep")
    signal = pd.Series(0.0, index=factor_series.index, dtype=float)
    signal[rank >= 1 - top_quantile] = 1.0   # top 20% → 做多
    signal[rank <= top_quantile] = -1.0        # bottom 20% → 做空
    # 末尾 horizon 根无前瞻收益，信号置 NaN
    if horizon > 0:
        signal.iloc[-horizon:] = float("nan")
    return signal


def _simulate_backtest(
    signal: pd.Series,
    close: pd.Series,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
) -> dict[str, Any]:
    """按信号时序模拟简单回测，返回绩效指标。

    每根 bar 收盘时按信号调整仓位（次日开盘价换仓，简化以收盘价近似）。
    只做方向性交易（long/short），不涉及仓位管理。

    Args:
        signal: 信号时序（+1/-1/0，末尾 NaN 表示无信号）。
        close: 收盘价时序（与 signal 同索引）。
        initial_cash: 起始现金。
        fee_rate: 单边手续费率。

    Returns:
        dict 包含 equity_curve, returns, sharpe, max_drawdown_pct, total_return_pct。
    """
    valid = signal.notna() & close.notna()
    if valid.sum() < 2:
        return {
            "equity_curve": [float(initial_cash)],
            "returns": [],
            "sharpe": None,
            "max_drawdown_pct": 0.0,
            "total_return_pct": 0.0,
            "win_rate": None,
            "num_trades": 0,
        }

    sig = signal[valid].values
    prc = close[valid].values
    n = len(sig)

    # 逐 bar 模拟
    cash = float(initial_cash)
    position = 0.0  # 持仓数量（正=多，负=空）
    equity_curve = [cash]
    trades = []  # [(entry_bar, exit_bar, pnl)]

    for i in range(1, n):
        prev_sig = sig[i - 1]
        cur_sig = sig[i]

        # 换仓信号
        if cur_sig != prev_sig and prev_sig != 0:
            # 平掉旧仓位
            exit_price = prc[i - 1]
            trade_pnl = position * (exit_price - prc[i - 2]) if i >= 2 else 0.0
            fee = abs(position) * exit_price * fee_rate
            cash += trade_pnl - fee
            position = 0.0
            if trade_pnl != 0:
                trades.append(trade_pnl)

        if cur_sig != prev_sig and cur_sig != 0:
            # 开新仓位
            entry_price = prc[i - 1]
            position = cur_sig * (cash * 0.99 / entry_price)  # 留 1% 手续费空间
            fee = abs(position) * entry_price * fee_rate
            cash -= fee
            cash -= position * entry_price

        # 每日 mark-to-market
        equity = cash + position * prc[i - 1]
        equity_curve.append(equity)

    # 末根 bar 平仓
    if position != 0:
        exit_price = prc[-1]
        trade_pnl = position * (exit_price - prc[-2]) if n >= 2 else 0.0
        fee = abs(position) * exit_price * fee_rate
        cash += trade_pnl - fee
        position = 0.0
        equity_curve[-1] = cash

    # 计算指标
    eq = np.array(equity_curve)
    returns = eq[1:] / eq[:-1] - 1.0
    total_return = (eq[-1] / eq[0] - 1.0) * 100.0 if eq[0] > 0 else 0.0

    # 年化 Sharpe（简化：假设日频）
    if len(returns) >= 2 and returns.std() > 0:
        sharpe = float(returns.mean() / returns.std() * np.sqrt(252))
    else:
        sharpe = None

    # 最大回撤
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100.0
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

    # 胜率
    trade_pnls = [t for t in trades if t != 0]
    win_rate = (
        sum(1 for p in trade_pnls if p > 0) / len(trade_pnls) * 100.0
        if trade_pnls
        else None
    )

    return {
        "equity_curve": [float(v) for v in equity_curve],
        "returns": [float(r) for r in returns],
        "sharpe": sharpe,
        "max_drawdown_pct": min(max_dd, 100.0),
        "total_return_pct": float(total_return),
        "win_rate": win_rate,
        "num_trades": len(trades),
    }


# ── WalkForward 拆分 + 每段回测 ──────────────────────────────────────


def _walk_forward_signal(
    signal: pd.Series,
    close: pd.Series,
    n_splits: int = 5,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
) -> dict[str, Any]:
    """WalkForward 多段回测：每段在 train 上"评估"（无参数，信号已有），
    在 test 段上取绩效，最终聚合成 OOS 分布。

    Args:
        signal: 信号时序（已含 NaN warmup 段和末尾 horizon 空白）。
        close: 收盘价时序。
        n_splits: 切分段数（默认 5，末段作 test）。
        initial_cash / fee_rate: 透传给回测模拟。

    Returns:
        dict 包含 OOS 指标分布。
    """
    n = len(signal)
    valid = signal.notna() & close.notna()
    if valid.sum() < 2:
        return {}

    # 简单 WalkForward 切分：按时间均分，每段逐步前移
    test_size = max(1, n // (n_splits + 1))
    train_size = n - test_size * 2  # 最小训练量

    oos_sharpes: list[float] = []
    oos_max_dds: list[float] = []
    oos_returns: list[float] = []
    oos_win_rates: list[float] = []

    for k in range(n_splits):
        test_end = n - (n_splits - 1 - k) * test_size
        test_start = max(0, test_end - test_size)
        train_start = max(0, test_start - train_size)

        if test_start <= train_start or test_start >= n:
            continue

        # 在 test 段上模拟
        test_signal = signal.iloc[test_start:test_end]
        test_close = close.iloc[test_start:test_end]

        if test_signal.notna().sum() < 2:
            continue

        bt = _simulate_backtest(
            test_signal, test_close,
            initial_cash=initial_cash, fee_rate=fee_rate,
        )

        if bt["sharpe"] is not None:
            oos_sharpes.append(bt["sharpe"])
        oos_max_dds.append(bt["max_drawdown_pct"])
        oos_returns.append(bt["total_return_pct"])
        if bt["win_rate"] is not None:
            oos_win_rates.append(bt["win_rate"])

    if not oos_sharpes:
        return {}

    # 全样本 In-Sample Sharpe（作为对比基线）
    is_bt = _simulate_backtest(signal, close, initial_cash=initial_cash, fee_rate=fee_rate)
    insample_sharpe = is_bt["sharpe"]

    oos_mean = float(np.mean(oos_sharpes))
    oos_std = float(np.std(oos_sharpes)) if len(oos_sharpes) > 1 else 0.0

    # 退化率
    degradation = None
    if insample_sharpe is not None and insample_sharpe != 0:
        degradation = (insample_sharpe - oos_mean) / abs(insample_sharpe)

    return {
        "oos_ic_mean": oos_mean,
        "oos_ic_std": oos_std,
        "oos_ic_p50": float(np.median(oos_sharpes)),
        "oos_ic_p5": float(np.percentile(oos_sharpes, 5)) if len(oos_sharpes) >= 5 else None,
        "oos_ic_p95": float(np.percentile(oos_sharpes, 95)) if len(oos_sharpes) >= 5 else None,
        "insample_ic": insample_sharpe,
        "degradation_rate": degradation,
        "n_splits": n_splits,
        # 回测指标
        "oos_sharpe": oos_mean,
        "oos_sharpe_p5": float(np.percentile(oos_sharpes, 5)) if len(oos_sharpes) >= 5 else None,
        "oos_sharpe_p95": float(np.percentile(oos_sharpes, 95)) if len(oos_sharpes) >= 5 else None,
        "oos_max_drawdown_pct": float(np.mean(oos_max_dds)) if oos_max_dds else None,
        "oos_win_rate": float(np.mean(oos_win_rates)) if oos_win_rates else None,
        "oos_return_pct": float(np.mean(oos_returns)) if oos_returns else None,
        "baseline_sharpe": None,
        "dsr": None,
        "n_paths": len(oos_sharpes),
        "splitter_used": "walk_forward",
    }


# ── 主入口 ────────────────────────────────────────────────────────────


async def backtest_score(
    engine: FactorEngine,
    expression: str,
    name: str | None,
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime | None,
    lookback_bars: int,
    horizon_bars: int,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    cv_splitter: str = "walk_forward",
    cv_n_folds: int = 5,
    cv_embargo_pct: float = 0.05,
) -> dict[str, Any]:
    """因子评估 → 自动回测闭环。

    1. 解析表达式
    2. 取数据（同 custom_score）
    3. 评估因子有效性
    4. 构造信号
    5. WalkForward 回测
    6. 返回合并结果
    """
    # 1. 解析表达式
    parsed = parse_expression(expression)

    # 2. 取数据
    now = datetime.now(UTC)
    is_live = as_of is None or as_of >= now - timedelta(seconds=_tf_seconds(timeframe) * 2)
    as_of = as_of or now
    span_bars = lookback_bars + horizon_bars + 60
    from_ts = as_of - timedelta(seconds=_tf_seconds(timeframe) * span_bars)

    df = await engine._fetch_df(
        venue=venue, symbol=symbol, timeframe=timeframe,
        from_ts=from_ts, to_ts=as_of, fresh=is_live,
    )
    if not df.empty and isinstance(df.index, pd.DatetimeIndex):
        df = df[df.index <= pd.Timestamp(as_of)]

    if df.empty:
        return {
            "available": False,
            "reason": "no bars from data-service",
            "expression": expression,
            "factor": None,
            "ic_pvalue": None,
            "top_correlated": [],
            "max_corr": None,
            "is_likely_redundant": False,
            "backtest": None,
        }

    # 3. 评估因子有效性
    series = evaluate(parsed, df)
    close = df["close"].astype(float)
    eff = score_factor(
        series, close,
        horizon=horizon_bars,
        quantiles=5,
        min_samples=engine._settings.min_effective_samples,
    )
    pval = ic_pvalue(eff.rank_ic, eff.sample_size, horizon_bars)

    # 与库因子去相关对比
    lib = engine.compute_on_df(df, engine._computable_ids(timeframe, exclude_macro=True))
    corrs: list[tuple[str, float]] = []
    for fid, s in lib.items():
        c = _abs_spearman(series, s)
        if c is not None:
            corrs.append((fid, c))
    corrs.sort(key=lambda t: t[1], reverse=True)
    max_corr = corrs[0][1] if corrs else None
    threshold = engine._settings.snapshot_corr_threshold
    is_redundant = bool(max_corr is not None and max_corr >= threshold)

    expr_hash = hashlib.sha256(expression.encode("utf-8")).hexdigest()[:16]
    spec = FactorSpec(
        f"custom.{expr_hash}",
        "custom",
        name or (expression if len(expression) <= 60 else expression[:57] + "..."),
        "custom",
        extras={"expression": expression},
    )
    factor_dict = _eff_to_dict(spec, eff)

    # 4. 构造信号 + 5. WalkForward 回测
    signal = _build_signal_from_factor(series, close, horizon=horizon_bars)
    wf_result = _walk_forward_signal(
        signal, close,
        n_splits=cv_n_folds,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
    )

    backtest_result = None
    if wf_result:
        backtest_result = {
            "oos_sharpe": wf_result["oos_sharpe"],
            "oos_sharpe_p5": wf_result["oos_sharpe_p5"],
            "oos_sharpe_p95": wf_result["oos_sharpe_p95"],
            "oos_max_drawdown_pct": wf_result["oos_max_drawdown_pct"],
            "oos_win_rate": wf_result["oos_win_rate"],
            "oos_return_pct": wf_result["oos_return_pct"],
            "baseline_sharpe": None,
            "dsr": None,
            "n_paths": wf_result["n_paths"],
            "splitter_used": wf_result["splitter_used"],
        }

    return {
        "available": True,
        "reason": None,
        "expression": expression,
        "factor": factor_dict,
        "ic_pvalue": pval,
        "top_correlated": [
            {"factor_id": fid, "corr": c} for fid, c in corrs[:5]
        ],
        "max_corr": max_corr,
        "is_likely_redundant": is_redundant,
        "backtest": backtest_result,
    }


def _abs_spearman(a: pd.Series | None, b: pd.Series | None) -> float | None:
    """两颗因子时序的 |spearman|；样本不足 / 常数列返回 None。"""
    if a is None or b is None:
        return None
    pair = pd.concat([a, b], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
    if len(pair) < 30:
        return None
    ar = pair.iloc[:, 0].rank()
    br = pair.iloc[:, 1].rank()
    if ar.std(ddof=0) == 0 or br.std(ddof=0) == 0:
        return None
    c = ar.corr(br)
    return None if np.isnan(c) else abs(float(c))


def _eff_to_dict(spec, eff) -> dict[str, Any]:
    """将 FactorSpec + EffResult 转为 dict（复用 engine 的私有方法）。"""
    return _engine_eff_to_dict(spec, eff)