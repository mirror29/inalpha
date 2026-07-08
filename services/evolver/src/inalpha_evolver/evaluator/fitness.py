"""fitness 合成 —— 薄封装 paper 的 ``compose_fitness``。

E1 不重新实现 fitness 公式，直接调用 paper 已有函数，确保评估口径一致。
"""
from __future__ import annotations

from inalpha_paper.engine.metrics import periods_per_year
from inalpha_paper.strategy_authoring.fitness import (
    FitnessInputs,
    compose_fitness,
)

from ..exceptions import EvaluationError
from ..population import EvaluationResult


def compute_fitness_from_report(report: dict, timeframe: str = "1h") -> float:
    """从 ``BacktestReport`` dict 合成 fitness。

    Args:
        report: 序列化的 BacktestReport（应含 sharpe, calmar, max_drawdown_pct,
                num_trades, num_bars_processed）。
        timeframe: timeframe 字符串（如 1h, 4h, 1d），用于年化。

    Returns:
        合成的 fitness 标量。DD > 30% 时 => -1e9（一票否决）。

    Raises:
        EvaluationError: 报告缺少必要字段。
    """
    try:
        inputs = FitnessInputs(
            sharpe=report.get("sharpe"),
            calmar=report.get("calmar"),
            max_drawdown_pct=report.get("max_drawdown_pct", 0.0),
            num_trades=report.get("num_trades", 0),
            num_bars_processed=report.get("num_bars_processed", 0),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationError(f"回测报告字段不足：{exc}") from exc

    fitness = compose_fitness(inputs)

    # 额外回撤 veto：DD > 30% => -1e9（比 compose_fitness 的 -1.0 更严厉）
    dd = report.get("max_drawdown_pct", 0.0)
    if dd > 30.0:
        return -1e9

    return fitness