"""E1 must preserve execution settings all the way to the real Paper engine."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from inalpha_paper.evaluation_worker import run_engine_worker

from inalpha_evolver.runtime import executor

from .test_frozen_evaluator import _dataset

SHORT_SOURCE = """
class ShortProbe(Strategy):
    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h"):
        super().__init__(name, clock, msgbus)
        self.instrument = instrument_id
        self.timeframe = timeframe
        self.count = 0
        self.short = False

    def on_start(self):
        self.subscribe_bars(self.instrument, self.timeframe)

    def on_position_opened(self, event):
        self.short = event.quantity < 0

    def on_bar(self, bar):
        self.count += 1
        if self.count == 1 or (self.count == 3 and self.short):
            side = OrderSide.SELL if self.count == 1 else OrderSide.BUY
            self.submit_order(Order(
                client_order_id=ClientOrderId("probe-" + str(self.count)),
                instrument_id=self.instrument, side=side,
                type=OrderType.MARKET, quantity=1.0,
            ))
"""


@pytest.mark.asyncio
async def test_e1_executor_preserves_short_execution_mode(monkeypatch):
    dataset = _dataset()

    @asynccontextmanager
    async def context(*args, **kwargs):
        yield object()

    async def runner(**kwargs):
        return run_engine_worker(**kwargs)

    observed = []

    async def generation(run, *, mutator, evaluator):
        result = await evaluator.evaluate(SHORT_SOURCE)
        observed.append((evaluator.trading_mode, evaluator.leverage, result.report["num_trades"]))

    monkeypatch.setattr(executor, "DataClient", context)
    monkeypatch.setattr(executor, "get_conn", context)
    monkeypatch.setattr(
        executor,
        "FrozenBarsLoader",
        lambda client: SimpleNamespace(load=AsyncMock(return_value=dataset)),
    )
    monkeypatch.setattr(executor, "KillableEngineRunner", lambda **kwargs: runner)
    monkeypatch.setattr(executor.runs, "transition", AsyncMock(return_value={}))
    monkeypatch.setattr(executor, "execute_generation", generation)
    monkeypatch.setattr(executor, "_service_token", lambda *args: "test-token")
    settings = SimpleNamespace(
        data_service_url="http://data.test", evolver_job_timeout_s=10, evolver_job_mem_gb=1
    )
    run = {
        "run_id": uuid4(),
        "owner_account_id": uuid4(),
        "config": {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "from_ts": "2026-01-01T00:00:00Z",
            "as_of": "2026-01-02T17:00:00Z",
            "initial_cash": 10_000,
            "trading_mode": "perp",
            "leverage": 3,
        },
    }
    await executor.execute_run(run, mutator=object(), settings=settings)
    assert observed == [("perp", 3, 2)]

    run["config"].pop("trading_mode")
    run["config"].pop("leverage")
    await executor.execute_run(run, mutator=object(), settings=settings)
    assert observed[-1] == ("spot", 1, 0)
