"""冻结数据集上的真实策略评估。"""

from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from inalpha_paper.evaluation_executor import KillableEngineRunner
from inalpha_paper.execution.exchange import EventExecutionPolicy
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.market_events import MarketEvent
from inalpha_paper.strategy_evaluation import (
    evaluate_buy_and_hold,
    evaluate_strategy_source,
)

from ..data import FrozenDataset
from ..population import EvaluationResult


@dataclass(slots=True)
class FrozenDatasetEvaluator:
    """所有候选复用同一份 frozen bars 与年化参数。"""

    dataset: FrozenDataset
    runner: KillableEngineRunner
    initial_cash: float = 10_000.0
    fee_rate: float = 0.001
    validation_split: float = 0.3
    trading_mode: str = "spot"
    leverage: int = 1
    funding_rate: float = 0.0
    protective_stop_loss_pct: float | None = None
    protective_take_profit_pct: float | None = None
    protective_trailing_stop_pct: float | None = None
    protective_chandelier_atr_mult: float | None = None
    protective_chandelier_atr_period: int = 22
    params: dict[str, Any] = field(default_factory=dict)
    events: tuple[MarketEvent, ...] = ()
    event_execution_policy: EventExecutionPolicy | None = None

    async def evaluate_baseline(self) -> dict:
        """在同一 frozen bars 上计算一次市场买入持有基准。"""
        manifest = self.dataset.manifest
        instrument = InstrumentId(symbol=manifest.symbol, venue=manifest.venue)
        result = await evaluate_buy_and_hold(
            bars=list(self.dataset.bars),
            instrument_id=instrument,
            timeframe=manifest.canonical_timeframe,
            run_engine=self.runner,
            initial_cash=self.initial_cash,
            fee_rate=self.fee_rate,
            annualization_periods=float(manifest.annualization_periods),
        )
        return result.snapshot.model_dump(mode="json")

    async def evaluate(
        self,
        source_code: str,
        *,
        event_scope: dict[str, Any] | None = None,
    ) -> EvaluationResult:
        manifest = self.dataset.manifest
        instrument = InstrumentId(symbol=manifest.symbol, venue=manifest.venue)
        result = await evaluate_strategy_source(
            source_code=source_code,
            params=self.params,
            bars=list(self.dataset.bars),
            instrument_id=instrument,
            timeframe=manifest.canonical_timeframe,
            run_engine=self.runner,
            initial_cash=self.initial_cash,
            fee_rate=self.fee_rate,
            validation_split=self.validation_split,
            annualization_periods=float(manifest.annualization_periods),
            events=list(self.events),
            event_execution_policy=self.event_execution_policy,
            trading_mode=self.trading_mode,
            leverage=self.leverage,
            funding_rate=self.funding_rate,
            protective_stop_loss_pct=self.protective_stop_loss_pct,
            protective_take_profit_pct=self.protective_take_profit_pct,
            protective_trailing_stop_pct=self.protective_trailing_stop_pct,
            protective_chandelier_atr_mult=self.protective_chandelier_atr_mult,
            protective_chandelier_atr_period=self.protective_chandelier_atr_period,
        )
        return EvaluationResult(
            report=result.snapshot.model_dump(mode="json"),
            fitness=result.snapshot.fitness,
            data_epoch=int(manifest.latest_bar_ts.timestamp() * 1000),
            overfitting_risk=_risk(result.snapshot.validation),
            behavior=_behavior_fingerprint(result.report, list(self.dataset.bars)),
            execution_event_metrics=_execution_event_metrics(
                result.report,
                list(self.dataset.bars),
                list(self.events),
                event_scope,
            ),
        )


def _risk(validation: object | None) -> str:
    if validation is None:
        return "high"
    flags = getattr(validation, "flags", [])
    decay = getattr(validation, "decay_ratio", None)
    if flags or decay is None or decay < 0.5:
        return "high"
    return "low" if decay >= 0.8 else "medium"


def _behavior_fingerprint(report: Any, bars: list[Any]) -> dict[str, list[float]]:
    """Compress signal, trade, and holding behavior into bounded comparable vectors."""
    known_times = [int(bar.bar_known_at) for bar in bars]
    fills = list(report.fills)
    bins = [0.0] * 32
    fill_indices: list[int] = []
    buy_count = 0
    for fill in fills:
        index = min(len(bars) - 1, bisect.bisect_left(known_times, int(fill.ts_ns)))
        fill_indices.append(index)
        bins[min(31, int(index / max(1, len(bars)) * 32))] = 1.0
        buy_count += int(str(fill.side).upper() == "BUY")
    gaps = [right - left for left, right in pairwise(fill_indices)]
    return {
        "signal": bins,
        "trade": [
            min(1.0, len(fills) / max(1, len(bars))),
            buy_count / max(1, len(fills)),
            float(report.total_fees) / max(1.0, float(report.initial_cash)),
        ],
        "holding": [
            float(report.exposure_pct or 0.0) / 100.0,
            (sum(gaps) / len(gaps) / max(1, len(bars))) if gaps else 0.0,
            (max(gaps) / max(1, len(bars))) if gaps else 0.0,
        ],
    }


def _execution_event_metrics(
    report: Any,
    bars: list[Any],
    events: list[MarketEvent],
    scope: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Measure scoped reactions on the actual post-fill, post-fee equity path."""
    if scope is None or not report.equity_curve:
        return None
    event_types = set(scope.get("event_types") or ())
    asset = str(scope.get("asset") or "").upper()
    min_severity = float(scope.get("min_severity") or 0.0)
    min_confidence = float(scope.get("min_confidence") or 0.0)
    holding_bars = max(1, int(scope.get("holding_bars") or 1))
    scoped = [
        event
        for event in events
        if event.event_type in event_types
        and (not event.assets or asset in event.assets)
        and event.severity >= min_severity
        and event.confidence >= min_confidence
    ]
    independent: list[MarketEvent] = []
    last: dict[tuple[str, str], int] = {}
    for event in sorted(scoped, key=lambda item: (item.available_at, item.event_id)):
        key = (asset, event.event_type)
        if key in last and event.available_at - last[key] < 24 * 60 * 60 * 1_000_000_000:
            continue
        last[key] = event.available_at
        independent.append(event)
    equity_times = [int(item[0]) for item in report.equity_curve]
    equity_values = [float(item[1]) for item in report.equity_curve]
    fill_times = sorted(int(fill.ts_ns) for fill in report.fills)
    effects: list[float] = []
    delays: list[int] = []
    reversals = 0
    missed = 0
    for event in independent:
        index = bisect.bisect_left(equity_times, event.available_at)
        if index >= len(equity_values) - holding_bars or equity_values[index] <= 0:
            missed += 1
            continue
        exit_index = index + holding_bars
        path = [
            (value / equity_values[index] - 1.0) * 100.0
            for value in equity_values[index + 1 : exit_index + 1]
        ]
        effect = path[-1]
        effects.append(effect)
        if path and max(path) > 0 > effect:
            reversals += 1
        fill_index = bisect.bisect_left(fill_times, event.available_at)
        if fill_index < len(fill_times) and fill_times[fill_index] <= equity_times[exit_index]:
            delays.append(bisect.bisect_left(equity_times, fill_times[fill_index]) - index)
        else:
            missed += 1
    event_windows = [
        (event.available_at, event.available_at + holding_bars * _bar_interval_ns(bars))
        for event in independent
    ]
    false_positive = sum(
        not any(start <= fill_time <= end for start, end in event_windows)
        for fill_time in fill_times
    )
    return {
        "event_count": len(effects),
        "event_effects": effects,
        "mean_post_cost_return_pct": statistics.mean(effects) if effects else 0.0,
        "positive_event_ratio": sum(value > 0 for value in effects) / len(effects)
        if effects
        else 0.0,
        "mean_reaction_delay_bars": statistics.mean(delays) if delays else None,
        "reversal_count": reversals,
        "missed_event_count": missed,
        "false_positive_count": false_positive,
        "total_fees": float(report.total_fees),
    }


def _bar_interval_ns(bars: list[Any]) -> int:
    if len(bars) < 2:
        return 0
    return max(0, int(bars[1].bar_known_at) - int(bars[0].bar_known_at))


__all__ = ["FrozenDatasetEvaluator", "_behavior_fingerprint"]
