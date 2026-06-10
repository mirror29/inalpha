"""多目标 fitness 合成（ADR-0020 §适应度函数，E1 起强制）。

**E1 不允许裸 Sharpe 排序候选**——必须用 fitness 合成。理由见 ADR-0020 §D4：
单目标 Sharpe 会被 LLM 卷出"高换手套利幻觉"或"忽视回撤"的策略。

公式（ADR-0020 §适应度函数）：

    fitness = sharpe
            + 0.3 * calmar
            - 0.10 * turnover_penalty       # turnover / 252（年化换手）
            - 1.0 * (drawdown > 0.30)       # 30% 回撤一票否决

MVP 留空：``capacity_penalty``（需 ADV 数据，D-10+ 引入）。
"""
from __future__ import annotations

from dataclasses import dataclass

# 30% 最大回撤一票否决阈值（ADR-0020 §适应度函数）
_DRAWDOWN_HARD_LIMIT: float = 0.30

# 系数
_W_CALMAR: float = 0.3
_W_TURNOVER: float = 0.10
_W_DRAWDOWN_VETO: float = 1.0


@dataclass(slots=True, frozen=True)
class FitnessInputs:
    """喂给 ``compose_fitness`` 的最小集。所有字段都从 ``BacktestReport`` 直接拿。

    任何字段为 ``None`` 表示样本不足 / 不可计算 —— compose_fitness 会以保守值兜底。
    """

    sharpe: float | None
    calmar: float | None
    max_drawdown_pct: float
    """0-100 区间的百分比（与 BacktestReport.max_drawdown_pct 一致）。"""

    num_trades: int
    num_bars_processed: int


def compose_fitness(inputs: FitnessInputs) -> float:
    """合成单标量 fitness。返回 ``float``，可正可负。

    保守化原则：

    - ``sharpe`` 缺失 → 视为 0（中性，不奖不罚）
    - ``calmar`` 缺失 → 视为 0
    - turnover 用 ``num_trades / num_bars_processed`` 近似换手率（每根 bar 平均交易数）
    - 30% 回撤一票否决：直接扣 1.0（量级和其它项可比；ADR-0020 §适应度函数）
    """
    sharpe = inputs.sharpe if inputs.sharpe is not None else 0.0
    calmar = inputs.calmar if inputs.calmar is not None else 0.0

    # turnover_penalty：每根 bar 平均交易数 × 100（量级与 Sharpe 可比）
    turnover_penalty = 0.0
    if inputs.num_bars_processed > 0:
        turnover_penalty = (inputs.num_trades / inputs.num_bars_processed) * 100.0

    # max_drawdown_pct 是 0-100 区间 → 转 0-1 比较
    drawdown_frac = max(0.0, inputs.max_drawdown_pct) / 100.0
    drawdown_veto = 1.0 if drawdown_frac > _DRAWDOWN_HARD_LIMIT else 0.0

    return (
        sharpe
        + _W_CALMAR * calmar
        - _W_TURNOVER * turnover_penalty
        - _W_DRAWDOWN_VETO * drawdown_veto
    )


def calmar_from_report(
    total_return_pct: float,
    max_drawdown_pct: float,
    num_bars_processed: int,
    bars_per_year: float,
) -> float | None:
    """从 BacktestReport 已有字段推 Calmar（年化收益 / 最大回撤）。

    Args:
        total_return_pct: 累计收益百分比（0-100 区间，可负）
        max_drawdown_pct: 最大回撤百分比（0-100 区间，正数）
        num_bars_processed: 实际 bar 数
        bars_per_year: 该 timeframe 一年大约多少 bar（年化用）

    Returns:
        Calmar 比率；回撤为 0 / 样本不足时返 ``None``。

    ⚠️ 单一入口约定：BacktestReport 已自带 ``calmar``（engine/metrics.calmar_ratio,
    与本函数同式），**指标落库一律用 report.calmar**。本函数仅作 fitness 合成的
    内部输入保留（runner._fitness_from_report）——若要改年化口径（如换复利），
    必须与 metrics.calmar_ratio 同步改，否则 fitness 与落库指标静默分叉。
    """
    if num_bars_processed <= 0 or bars_per_year <= 0:
        return None
    if max_drawdown_pct <= 0:
        return None
    years = num_bars_processed / bars_per_year
    if years <= 0:
        return None
    # 年化收益（按线性换算；ADR-0020 §适应度函数没指定复利，MVP 用线性近似）
    annual_return_pct = total_return_pct / years
    return annual_return_pct / max_drawdown_pct
