"""可 pickle 的回测 worker。"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from inalpha_shared.errors import ValidationError

from .engine.backtest import BacktestEngine
from .execution.exchange import EventExecutionPolicy
from .strategies import get_strategy_class
from .strategy_authoring import (
    ContractError,
    DynamicLoadError,
    audit_strategy_code,
    load_strategy_class,
    verify_strategy_contract,
)

if TYPE_CHECKING:
    from .engine.report import BacktestReport
    from .kernel.identifiers import InstrumentId
    from .model.data import Bar
    from .model.market_events import MarketEvent


def run_engine_worker(
    *,
    bars: list[Bar],
    instrument_id: InstrumentId,
    timeframe: str,
    strategy_id: str | None,
    params: dict[str, Any],
    initial_cash: float,
    fee_rate: float,
    candidate_code: str | None = None,
    protective_stop_loss_pct: float | None = None,
    protective_take_profit_pct: float | None = None,
    protective_trailing_stop_pct: float | None = None,
    protective_chandelier_atr_mult: float | None = None,
    protective_chandelier_atr_period: int = 22,
    trading_mode: str = "spot",
    leverage: int = 1,
    funding_rate: float = 0.0,
    annualization_periods: int | None = None,
    events: list[MarketEvent] | None = None,
    event_execution_policy: EventExecutionPolicy | None = None,
) -> BacktestReport:
    """实例化 engine 与策略并执行冻结 bars，不做 IO。"""
    engine = BacktestEngine(
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        protective_stop_loss_pct=protective_stop_loss_pct,
        protective_take_profit_pct=protective_take_profit_pct,
        protective_trailing_stop_pct=protective_trailing_stop_pct,
        protective_chandelier_atr_mult=protective_chandelier_atr_mult,
        protective_chandelier_atr_period=protective_chandelier_atr_period,
        trading_mode=trading_mode,
        leverage=leverage,
        funding_rate=funding_rate,
        annualization_periods=annualization_periods,
        event_execution_policy=event_execution_policy,
    )
    if candidate_code is not None:
        audit = audit_strategy_code(candidate_code)
        if not audit.ok:
            raise ValidationError(
                f"strategy source failed audit: {audit.reason()}",
                code="CANDIDATE_REAUDIT_FAILED",
            )
        try:
            strategy_cls = load_strategy_class(candidate_code)
        except DynamicLoadError as exc:
            raise ValidationError(
                f"strategy source failed to load: {exc}",
                code="CANDIDATE_LOAD_FAILED",
            ) from exc
        try:
            verify_strategy_contract(strategy_cls)
        except ContractError as exc:
            raise ValidationError(
                f"strategy source failed contract check: {exc}",
                code="CANDIDATE_CONTRACT_FAILED",
            ) from exc
        strategy_name = f"{strategy_cls.__name__}-{instrument_id.symbol}"
    else:
        if strategy_id is None:
            raise ValidationError(
                "internal: neither strategy_id nor candidate_code provided",
                code="STRATEGY_MISSING",
            )
        strategy_cls = get_strategy_class(strategy_id)
        strategy_name = f"{strategy_id}-{instrument_id.symbol}"

    strategy_kwargs: dict[str, Any] = dict(params)
    try:
        signature = inspect.signature(strategy_cls.__init__)
        if "initial_cash" in signature.parameters:
            strategy_kwargs.setdefault("initial_cash", initial_cash)
        if "position_pct" in signature.parameters:
            strategy_kwargs.setdefault("position_pct", 1.0)
    except (TypeError, ValueError):
        pass
    strategy = strategy_cls(  # type: ignore[call-arg]
        name=strategy_name,
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=instrument_id,
        timeframe=timeframe,
        **strategy_kwargs,
    )
    engine.add_strategy(strategy)
    return engine.run(bars, events=events)


__all__ = ["run_engine_worker"]
