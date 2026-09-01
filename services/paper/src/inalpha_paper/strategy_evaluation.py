from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from inalpha_shared.errors import ValidationError

from .engine.metrics import periods_per_year
from .evaluation_metrics import fitness_from_report, validation_from_report
from .evaluation_snapshot import EvaluationSnapshot
from .strategies import BASELINE_BUY_AND_HOLD
from .strategy_preparation import audit_strategy_source

if TYPE_CHECKING:
    from .engine.report import BacktestReport
    from .execution.exchange import EventExecutionPolicy
    from .kernel.identifiers import InstrumentId
    from .model.data import Bar
    from .model.market_events import MarketEvent

EngineRunner = Callable[..., Awaitable["BacktestReport"]]


@dataclass(frozen=True, slots=True)
class SourceEvaluation:
    report: BacktestReport
    snapshot: EvaluationSnapshot


async def evaluate_strategy_source(
    *,
    source_code: str,
    bars: list[Bar],
    instrument_id: InstrumentId,
    timeframe: str,
    run_engine: EngineRunner,
    params: dict[str, Any] | None = None,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    validation_split: float = 0.3,
    annualization_periods: float | None = None,
    trading_mode: str = "spot",
    leverage: int = 1,
    funding_rate: float = 0.0,
    events: list[MarketEvent] | None = None,
    event_execution_policy: EventExecutionPolicy | None = None,
) -> SourceEvaluation:
    """审计临时源码并在调用方提供的隔离执行器中评估。"""
    _validate_bars(bars)
    audited_source = audit_strategy_source(source_code)
    periods = annualization_periods or float(periods_per_year(timeframe))
    report = await run_engine(
        bars=bars,
        instrument_id=instrument_id,
        timeframe=timeframe,
        strategy_id=None,
        candidate_code=audited_source,
        params=params or {},
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        trading_mode=trading_mode,
        leverage=leverage,
        funding_rate=funding_rate,
        annualization_periods=int(periods),
        events=events,
        event_execution_policy=event_execution_policy,
    )
    validation, fitness = await asyncio.to_thread(
        _compute_metrics,
        report,
        validation_split,
        periods,
    )
    return SourceEvaluation(
        report=report,
        snapshot=EvaluationSnapshot.from_report(
            report,
            fitness=fitness,
            annualization_periods=periods,
            validation=validation,
        ),
    )


async def evaluate_buy_and_hold(
    *,
    bars: list[Bar],
    instrument_id: InstrumentId,
    timeframe: str,
    run_engine: EngineRunner,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    annualization_periods: float | None = None,
) -> SourceEvaluation:
    """在同一冻结数据集上评估 buy-and-hold 市场基准。"""
    _validate_bars(bars)
    fill_open = bars[1].open
    if fill_open <= 0:
        raise ValidationError(
            f"bar open price must be positive, got {fill_open}",
            code="INVALID_BAR_PRICE",
        )
    quantity = initial_cash / fill_open / (1.0 + fee_rate + 0.005)
    periods = annualization_periods or float(periods_per_year(timeframe))
    report = await run_engine(
        bars=bars,
        instrument_id=instrument_id,
        timeframe=timeframe,
        strategy_id=BASELINE_BUY_AND_HOLD,
        candidate_code=None,
        params={"trade_size": quantity},
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        annualization_periods=int(periods),
    )
    fitness = fitness_from_report(report, bars_per_year=periods)
    return SourceEvaluation(
        report=report,
        snapshot=EvaluationSnapshot.from_report(
            report,
            fitness=fitness,
            annualization_periods=periods,
            validation=None,
        ),
    )


def _compute_metrics(
    report: BacktestReport,
    validation_split: float,
    periods: float,
) -> tuple[Any, float]:
    """在线程中计算 bootstrap/fitness，避免阻塞 async API。"""
    validation = (
        validation_from_report(report, split=validation_split, bars_per_year=periods)
        if validation_split > 0
        else None
    )
    return validation, fitness_from_report(report, bars_per_year=periods)


def _validate_bars(bars: list[Bar]) -> None:
    if len(bars) < 2:
        raise ValidationError(
            f"strategy evaluation needs >= 2 bars, got {len(bars)}",
            code="NO_BARS_AVAILABLE",
        )
