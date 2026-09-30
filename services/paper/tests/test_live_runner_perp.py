"""live runner perp 集成测试:保证金购买力 gate + 资金费计提(DB)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn

from inalpha_paper.config import get_paper_settings
from inalpha_paper.data_client import DataClient
from inalpha_paper.engine.live_session import LiveEngineSession
from inalpha_paper.kernel.identifiers import ClientOrderId, InstrumentId
from inalpha_paper.live_runner import LiveRunnerManager
from inalpha_paper.model.data import Bar
from inalpha_paper.model.orders import Order, OrderSide, OrderType
from inalpha_paper.storage import accounts as accounts_store
from inalpha_paper.storage import run_wallets as wallets_store
from inalpha_paper.storage import strategy_candidates as candidates_store
from inalpha_paper.storage import strategy_runs as runs_store
from inalpha_paper.strategy.base import Strategy
from inalpha_paper.wallet_accounting import settle_funding

pytestmark = pytest.mark.integration

_PERP = InstrumentId(symbol="BTC/USDT:USDT", venue="binance")
_H = 3600 * 1_000_000_000


def _bar(ts_ns: int, close: float) -> Bar:
    return Bar(
        instrument_id=_PERP,
        timeframe="1h",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


class _SellOnce(Strategy):
    def __init__(self, name, clock, msgbus, instrument_id, timeframe, sell_qty=1.0, **_kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, clock, msgbus)
        self._iid = instrument_id
        self._tf = timeframe
        self._qty = sell_qty
        self._sent = False

    def on_start(self) -> None:
        self.subscribe_bars(self._iid, self._tf)

    def on_bar(self, bar: Bar) -> None:
        if not self._sent:
            self._sent = True
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId(f"s-{uuid4().hex[:8]}"),
                    instrument_id=self._iid,
                    side=OrderSide.SELL,
                    type=OrderType.MARKET,
                    quantity=self._qty,
                )
            )


def _perp_session(sell_qty: float, leverage: int = 5) -> LiveEngineSession:
    return LiveEngineSession(
        strategy_cls=_SellOnce,
        instrument_id=_PERP,
        timeframe="1h",
        params={"sell_qty": sell_qty},
        initial_cash=10_000.0,
        fee_rate=0.001,
        trading_mode="perp",
        leverage=leverage,
    )


async def _insert_perp_run(account_id: Any, leverage: int = 5) -> dict[str, Any]:
    async with get_conn() as conn:
        cid, _ = await candidates_store.insert_candidate(
            conn, code=f'"perp run test {uuid4().hex}"\n'
        )
        await accounts_store.get_or_create(conn, account_id)
        run = await runs_store.insert(
            conn,
            candidate_id=cid,
            account_id=account_id,
            venue="binance",
            symbol="BTC/USDT:USDT",
            timeframe="1h",
            params={},
            trading_mode="perp",
            leverage=leverage,
            allocation=Decimal(10000),
        )
        await wallets_store.create(
            conn,
            account_id,
            run["id"],
            Decimal(10000),
            quote_currency="USDT",
            quote_rate=Decimal(1),
        )
        return await runs_store.get(conn, run["id"])


async def _fund(account_id: Any, usdt: str) -> None:
    async with get_conn() as conn:
        await accounts_store.get_or_create(conn, account_id)
        await accounts_store.apply_cash_delta(
            conn, account_id, __import__("decimal").Decimal(usdt), currency="USDT"
        )


async def test_perp_margin_gate_rejects_empty_wallet(app_with_lifespan: Any) -> None:
    """空钱包下 perp 开空 → INSUFFICIENT_MARGIN 拒,不落账。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    run = await _insert_perp_run(account_id)
    async with get_conn() as conn:
        await wallets_store.apply_delta(
            conn, account_id, run["id"], Decimal(-10000), currency="USDT"
        )
    await manager._process_bar(_perp_session(1.0), run, _bar(0, 50_000.0))
    async with get_conn() as conn:
        decisions = await runs_store.list_decisions(conn, run["id"])
    assert len(decisions) == 1
    assert decisions[0]["outcome"] == "rejected"
    assert "INSUFFICIENT_MARGIN" in decisions[0]["reason"]


async def test_perp_margin_gate_allows_funded_wallet(app_with_lifespan: Any) -> None:
    """钱包够保证金 → perp 开空成交(裸空合法 + 保证金记账)。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    await _fund(account_id, "20000")  # IM = 1*100/5 = 20, 远小于 20000
    run = await _insert_perp_run(account_id, leverage=5)
    await manager._process_bar(_perp_session(1.0, leverage=5), run, _bar(0, 100.0))
    async with get_conn() as conn:
        from inalpha_paper.storage import positions as positions_store

        pos = await positions_store.get(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT:USDT", run_id=run["id"]
        )
    assert pos is not None and float(pos["quantity"]) == -1.0  # 开空成交
    assert float(pos["margin_used"]) == pytest.approx(20.0)  # IM=100/5


async def test_perp_funding_accrual(app_with_lifespan: Any) -> None:
    """Published settlement is applied once to the isolated wallet, including stopped books."""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    await _fund(account_id, "20000")
    run = await _insert_perp_run(account_id)
    await manager._process_bar(_perp_session(1.0), run, _bar(7 * _H, 100.0))
    settlement = datetime.now(UTC) + timedelta(seconds=1)
    item = {"ts": settlement.isoformat(), "mark_price": 100, "funding_rate": 0.001}
    async with get_conn() as conn:
        before = await wallets_store.get(conn, account_id, run["id"])
        main_before = await accounts_store.get(conn, account_id)
        await runs_store.set_status(conn, run["id"], "stopped")
        assert await settle_funding(conn, run, item)
        assert not await settle_funding(conn, run, item)
        after = await wallets_store.get(conn, account_id, run["id"])
        main_after = await accounts_store.get(conn, account_id)
    assert Decimal(after["cash_balances"]["USDT"]) - Decimal(
        before["cash_balances"]["USDT"]
    ) == Decimal("0.1")
    assert main_before["cash_balances"] == main_after["cash_balances"]
    restored = _perp_session(1.0)
    await manager._restore_position(restored, run)
    assert restored.portfolio.cash == pytest.approx(float(after["cash_balances"]["USDT"]))


class _ShortThenForgedCover(Strategy):
    """bar1 开空 1.0;bar2 发**伪造的超量** guard BUY(stop_loss tag + guard- 前缀,量 5)。"""

    def __init__(self, name, clock, msgbus, instrument_id, timeframe, **_kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, clock, msgbus)
        self._iid = instrument_id
        self._tf = timeframe
        self._n = 0

    def on_start(self) -> None:
        self.subscribe_bars(self._iid, self._tf)

    def on_bar(self, bar: Bar) -> None:
        self._n += 1
        if self._n == 1:
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId(f"s-{uuid4().hex[:8]}"),
                    instrument_id=self._iid,
                    side=OrderSide.SELL,
                    type=OrderType.MARKET,
                    quantity=1.0,
                )
            )
        elif self._n == 2:
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId(f"guard-{uuid4().hex[:8]}"),
                    instrument_id=self._iid,
                    side=OrderSide.BUY,
                    type=OrderType.MARKET,
                    quantity=5.0,
                    tag="stop_loss",
                )
            )


async def test_perp_protective_buy_clamped_to_short_no_flip(
    app_with_lifespan: Any,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """回归(CR major):伪造超量 guard BUY 被事务内 FOR UPDATE 钳到全平,绝不翻多。

    reduce-only 豁免只验方向——量 5 的 guard BUY(空头 -1)若不钳数量,会跳过 IM gate
    把仓位翻成 +4 多头。钳量后只平掉真实空头 → 持仓归 0。
    """

    async def _fake_funding(self, *, venue: str, symbol: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return {
            "symbol": symbol,
            "mark_price": 100.0,
            "funding_rate": 0.0,
            "ts": datetime(2026, 6, 26, tzinfo=UTC),
            "next_funding_ts": None,
        }

    monkeypatch.setattr(DataClient, "get_perp_funding", _fake_funding)

    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    await _fund(account_id, "20000")
    run = await _insert_perp_run(account_id, leverage=5)
    session = LiveEngineSession(
        strategy_cls=_ShortThenForgedCover,
        instrument_id=_PERP,
        timeframe="1h",
        params={},
        initial_cash=10_000.0,
        fee_rate=0.001,
        trading_mode="perp",
        leverage=5,
    )
    await manager._process_bar(session, run, _bar(0, 100.0))  # 开空 -1
    async with get_conn() as conn:
        run2 = await runs_store.get(conn, run["id"])
    await manager._process_bar(session, run2, _bar(_H, 100.0))  # 伪造超量 guard BUY 5

    async with get_conn() as conn:
        from inalpha_paper.storage import positions as positions_store

        pos = await positions_store.get(
            conn, account_id=account_id, venue="binance", symbol="BTC/USDT:USDT", run_id=run["id"]
        )
    assert pos is not None
    assert float(pos["quantity"]) == 0.0, f"expected flat (clamped), got {pos['quantity']}"


async def test_perp_funding_accrues_on_reused_run_dict(app_with_lifespan: Any) -> None:
    """Published settlement is applied once to the isolated wallet, including stopped books."""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()
    await _fund(account_id, "20000")
    run = await _insert_perp_run(account_id)
    await manager._process_bar(_perp_session(1.0), run, _bar(7 * _H, 100.0))
    settlement = datetime.now(UTC) + timedelta(seconds=1)
    item = {"ts": settlement.isoformat(), "mark_price": 100, "funding_rate": 0.001}
    async with get_conn() as conn:
        before = await wallets_store.get(conn, account_id, run["id"])
        main_before = await accounts_store.get(conn, account_id)
        await runs_store.set_status(conn, run["id"], "stopped")
        assert await settle_funding(conn, run, item)
        assert not await settle_funding(conn, run, item)
        after = await wallets_store.get(conn, account_id, run["id"])
        main_after = await accounts_store.get(conn, account_id)
    assert Decimal(after["cash_balances"]["USDT"]) - Decimal(
        before["cash_balances"]["USDT"]
    ) == Decimal("0.1")
    assert main_before["cash_balances"] == main_after["cash_balances"]
    restored = _perp_session(1.0)
    await manager._restore_position(restored, run)
    assert restored.portfolio.cash == pytest.approx(float(after["cash_balances"]["USDT"]))


class _ForgedGuardBuy(Strategy):
    """空仓提交伪造 guard 前缀 + 保护 tag 的 BUY(模拟想借风控豁免开大仓)。"""

    def __init__(self, name, clock, msgbus, instrument_id, timeframe, **_kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, clock, msgbus)
        self._iid = instrument_id
        self._tf = timeframe
        self._sent = False

    def on_start(self) -> None:
        self.subscribe_bars(self._iid, self._tf)

    def on_bar(self, bar: Bar) -> None:
        if not self._sent:
            self._sent = True
            self.submit_order(
                Order(
                    client_order_id=ClientOrderId(f"guard-{self._iid.symbol}-{uuid4().hex[:8]}"),
                    instrument_id=self._iid,
                    side=OrderSide.BUY,
                    type=OrderType.MARKET,
                    quantity=1.0,
                    tag="stop_loss",
                )
            )


async def test_forged_guard_buy_not_exempt_reduce_only(app_with_lifespan: Any) -> None:
    """伪造 guard+保护 tag 的 BUY(空仓开仓,非 reduce-only)→ 不享豁免 → 被 perp 保证金 gate 拦。"""
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    account_id = uuid4()  # 空钱包
    run = await _insert_perp_run(account_id, leverage=5)
    async with get_conn() as conn:
        await wallets_store.apply_delta(conn,account_id,run["id"],Decimal(-10000),currency="USDT")
    session = LiveEngineSession(
        strategy_cls=_ForgedGuardBuy,
        instrument_id=_PERP,
        timeframe="1h",
        params={},
        initial_cash=10_000.0,
        fee_rate=0.001,
        trading_mode="perp",
        leverage=5,
    )
    await manager._process_bar(session, run, _bar(0, 100.0))
    async with get_conn() as conn:
        decisions = await runs_store.list_decisions(conn, run["id"])
    # reduce-only 校验:flat 下 BUY 不是平仓 → 不豁免 → 走 margin gate → 空钱包拒
    assert len(decisions) == 1 and decisions[0]["outcome"] == "rejected"
    assert "INSUFFICIENT_MARGIN" in decisions[0]["reason"]
