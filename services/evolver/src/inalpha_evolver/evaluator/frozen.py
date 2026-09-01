"""冻结数据集上的真实策略评估。"""

from __future__ import annotations

from dataclasses import dataclass

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

    async def evaluate(self, source_code: str) -> EvaluationResult:
        manifest = self.dataset.manifest
        instrument = InstrumentId(symbol=manifest.symbol, venue=manifest.venue)
        result = await evaluate_strategy_source(
            source_code=source_code,
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
        )
        return EvaluationResult(
            report=result.snapshot.model_dump(mode="json"),
            fitness=result.snapshot.fitness,
            data_epoch=int(manifest.latest_bar_ts.timestamp() * 1000),
            overfitting_risk=_risk(result.snapshot.validation),
        )


def _risk(validation: object | None) -> str:
    if validation is None:
        return "high"
    flags = getattr(validation, "flags", [])
    decay = getattr(validation, "decay_ratio", None)
    if flags or decay is None or decay < 0.5:
        return "high"
    return "low" if decay >= 0.8 else "medium"


__all__ = ["FrozenDatasetEvaluator"]
