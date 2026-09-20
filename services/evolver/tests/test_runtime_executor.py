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
    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h", trade_size=1.0):
        super().__init__(name, clock, msgbus)
        self.instrument = instrument_id
        self.timeframe = timeframe
        self.count = 0
        self.short = False
        self.trade_size = trade_size

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
                type=OrderType.MARKET, quantity=self.trade_size,
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
    fees = []

    async def generation(run, *, mutator, evaluator):
        result = await evaluator.evaluate(SHORT_SOURCE)
        observed.append((evaluator.trading_mode, evaluator.leverage, result.report["num_trades"]))
        fees.append(result.report["total_fees"])

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
            "params": {"trade_size": 3},
        },
    }
    await executor.execute_run(run, mutator=object(), settings=settings)
    assert observed == [("perp", 3, 2)]

    run["config"].pop("params")
    await executor.execute_run(run, mutator=object(), settings=settings)
    assert fees[0] == pytest.approx(fees[1] * 3)

    run["config"].pop("trading_mode")
    run["config"].pop("leverage")
    await executor.execute_run(run, mutator=object(), settings=settings)
    assert observed[-1] == ("spot", 1, 0)


@pytest.mark.asyncio
async def test_parameterized_e1_matches_paper_and_differs_from_default():
    from inalpha_evolver.evaluator.frozen import FrozenDatasetEvaluator

    async def runner(**kwargs):
        return run_engine_worker(**kwargs)

    dataset = _dataset()
    evaluator = FrozenDatasetEvaluator(
        dataset=dataset, runner=runner, trading_mode="perp", leverage=3,
        params={"trade_size": 3.0},
    )
    actual = await evaluator.evaluate(SHORT_SOURCE)
    expected = run_engine_worker(
        bars=list(dataset.bars), instrument_id=dataset.bars[0].instrument_id,
        timeframe="1h", strategy_id=None, candidate_code=SHORT_SOURCE,
        params={"trade_size": 3.0}, initial_cash=10_000, fee_rate=0.001,
        trading_mode="perp", leverage=3,
    )
    assert actual.report["total_fees"] == expected.total_fees
    assert actual.report["final_equity"] == expected.final_equity
    evaluator.params = {}
    default = await evaluator.evaluate(SHORT_SOURCE)
    assert default.report["total_fees"] != actual.report["total_fees"]


@pytest.mark.asyncio
async def test_frozen_perpetual_evaluation_accounts_for_funding():
    from inalpha_evolver.evaluator.frozen import FrozenDatasetEvaluator

    async def runner(**kwargs):
        return run_engine_worker(**kwargs)

    source = SHORT_SOURCE.replace("self.count == 3", "self.count == 20")
    evaluator = FrozenDatasetEvaluator(
        dataset=_dataset(), runner=runner, trading_mode="perp", funding_rate=0.001,
    )
    funded = await evaluator.evaluate(source)
    evaluator.funding_rate = 0
    unfunded = await evaluator.evaluate(source)
    assert funded.report["final_equity"] > unfunded.report["final_equity"]


@pytest.mark.asyncio
async def test_frozen_evaluation_executes_protective_stop():
    from inalpha_evolver.evaluator.frozen import FrozenDatasetEvaluator

    async def runner(**kwargs):
        return run_engine_worker(**kwargs)

    source = SHORT_SOURCE.replace("self.count == 3", "self.count == 30")
    evaluator = FrozenDatasetEvaluator(
        dataset=_dataset(), runner=runner, trading_mode="perp",
        protective_stop_loss_pct=0.02,
    )
    protected = await evaluator.evaluate(source)
    evaluator.protective_stop_loss_pct = None
    unprotected = await evaluator.evaluate(source)
    assert protected.report["protective_exits"] == 1
    assert unprotected.report["protective_exits"] == 0
    assert protected.report["final_equity"] > unprotected.report["final_equity"]
