"""Real PostgreSQL regressions for separated books and conserved capital."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn
from psycopg.errors import ForeignKeyViolation

from inalpha_paper.config import get_paper_settings
from inalpha_paper.data_client import DataClient
from inalpha_paper.fills import apply_fill_to_positions_and_cash
from inalpha_paper.fx import BaseCurrencyConverter
from inalpha_paper.live_runner import LiveRunnerManager
from inalpha_paper.storage import (
    accounts,
    orders,
    positions,
    run_wallets,
    strategy_candidates,
    strategy_runs,
)
from inalpha_paper.wallet_accounting import settle_funding, snapshot

from .test_live_runner import _make_session


async def book(owner: Any, amount: str = "1000", mode: str = "spot") -> dict[str, Any]:
    async with get_conn() as conn:
        await accounts.get_or_create(conn, owner)
        cid, _ = await strategy_candidates.insert_candidate(
            conn, code=f'"wallet regression {uuid4()}"'
        )
        await accounts.get_or_create(conn, owner, for_update=True)
        run = await strategy_runs.insert(
            conn,
            candidate_id=cid,
            account_id=owner,
            venue="binance",
            symbol="BTC/USDT" if mode == "spot" else "BTC/USDT:USDT",
            timeframe="1h",
            allocation=Decimal(amount),
            trading_mode=mode,
            leverage=5 if mode == "perp" else 1,
        )
        await run_wallets.create(
            conn, owner, run["id"], Decimal(amount), quote_currency="USDT", quote_rate=Decimal(1)
        )
        return await strategy_runs.get(conn, run["id"])


async def fill(
    owner: Any,
    run: dict[str, Any] | None,
    side: str,
    quantity: str,
    price: str,
    fee: str = "1",
    ts: datetime | None = None,
) -> None:
    async with get_conn() as conn:
        oid = f"isolation-{uuid4()}"
        symbol = run["symbol"] if run else "BTC/USDT"
        run_id = run["id"] if run else None
        when = ts or datetime.now(UTC)
        await accounts.get_or_create(conn, owner, for_update=True)
        if run:
            await run_wallets.lock(conn, run)
        await orders.insert(
            conn,
            account_id=owner,
            client_order_id=oid,
            venue="binance",
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=Decimal(quantity),
            price=None,
            status="FILLED",
            filled_quantity=Decimal(quantity),
            avg_fill_price=Decimal(price),
            fee=Decimal(fee),
            notional=Decimal(quantity) * Decimal(price),
            ts_event=when,
            run_id=run_id,
            trading_mode=run["trading_mode"] if run else "spot",
        )
        await apply_fill_to_positions_and_cash(
            conn,
            account_id=owner,
            venue="binance",
            symbol=symbol,
            side=side,
            quantity=Decimal(quantity),
            fill_price=Decimal(price),
            fee=Decimal(fee),
            ts_event=when,
            order_id=oid,
            run_id=run_id,
            trading_mode=run["trading_mode"] if run else "spot",
            leverage=5 if run and run["trading_mode"] == "perp" else 1,
        )


async def test_old_position_new_run_restart_never_adopts_or_closes_legacy(
    app_with_lifespan: Any,
) -> None:
    owner = uuid4()
    await fill(owner, None, "BUY", "2", "100", ts=datetime(2026, 7, 1, tzinfo=UTC))
    run = await book(owner)
    manager = LiveRunnerManager(risk_guard_factory=None, settings=get_paper_settings())
    session = _make_session()
    await manager._restore_position(session, run)
    assert session.portfolio.cash == 1000
    assert session.portfolio.position(session.instrument_id) is None
    async with get_conn() as conn:
        equity, quote, base = await manager._read_run_pnl_quote(conn, run, 200)
        assert equity == 1000
        assert await manager._convert_run_pnl_to_base(run, equity, quote, base) == 0
        old = await positions.get(conn, account_id=owner, venue="binance", symbol="BTC/USDT")
        assert Decimal(old["quantity"]) == 2
    await fill(owner, run, "BUY", "1", "100")
    await fill(owner, run, "SELL", "1", "150")
    restarted = _make_session()
    await manager._restore_position(restarted, run)
    assert restarted.portfolio.cash == 1048
    async with get_conn() as conn:
        old = await positions.get(conn, account_id=owner, venue="binance", symbol="BTC/USDT")
        assert Decimal(old["quantity"]) == 2
        assert await positions.list_by_account(conn, owner, run_id=run["id"]) == []


async def test_same_symbol_manual_two_runs_and_other_user_are_independent(
    app_with_lifespan: Any,
) -> None:
    owner, other = uuid4(), uuid4()
    first, second, foreign = await book(owner), await book(owner), await book(other)
    await fill(owner, None, "BUY", "3", "80")
    await fill(owner, first, "BUY", "1", "100")
    await fill(owner, second, "BUY", "2", "120")
    await fill(other, foreign, "BUY", "4", "150")
    await fill(owner, first, "SELL", "0.5", "110")
    async with get_conn() as conn:
        for account, run, expected in [
            (owner, None, "3"),
            (owner, first, "0.5"),
            (owner, second, "2"),
            (other, foreign, "4"),
        ]:
            row = await positions.get(
                conn,
                account_id=account,
                venue="binance",
                symbol="BTC/USDT",
                run_id=run["id"] if run else None,
            )
            assert Decimal(row["quantity"]) == Decimal(expected)
        assert await run_wallets.get(conn, other, first["id"]) is None
        with pytest.raises(ForeignKeyViolation):
            async with conn.transaction():
                await positions.apply_fill(
                    conn,
                    account_id=other,
                    venue="binance",
                    symbol="BTC/USDT",
                    side="BUY",
                    fill_qty=Decimal(1),
                    fill_price=Decimal(10),
                    ts_event=datetime.now(UTC),
                    order_id="foreign",
                    run_id=first["id"],
                )


async def test_concurrent_allocations_cannot_overdraw_main_cash(app_with_lifespan: Any) -> None:
    owner = uuid4()
    results = await asyncio.gather(book(owner, "6000"), book(owner, "6000"), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, run_wallets.WalletConflict) for r in results) == 1
    async with get_conn() as conn:
        main = await accounts.get(conn, owner)
        wallets = await run_wallets.list_by_account(conn, owner)
        assert (
            Decimal(main["cash_balances"]["USD"])
            + sum(Decimal(w["cash_balances"]["USDT"]) for w in wallets)
            == 10000
        )
        assert len(wallets) == 1


async def test_release_requires_stopped_flat_and_is_idempotent_preserving_pnl(
    app_with_lifespan: Any,
) -> None:
    owner = uuid4()
    run = await book(owner)
    converter = BaseCurrencyConverter("USD", None)
    async with get_conn() as conn:
        with pytest.raises(run_wallets.WalletConflict):
            async with conn.transaction():
                await run_wallets.release(conn, run, converter)
    await fill(owner, run, "BUY", "1", "100")
    async with get_conn() as conn:
        await strategy_runs.set_status(conn, run["id"], "stopped")
        with pytest.raises(run_wallets.WalletConflict):
            async with conn.transaction():
                await run_wallets.release(conn, run, converter)
    await fill(owner, run, "SELL", "1", "150")
    async with get_conn() as conn:
        first = await run_wallets.release(conn, run, converter)
        second = await run_wallets.release(conn, run, converter)
        main = await accounts.get(conn, owner)
        record = await strategy_runs.get(conn, run["id"])
        assert first["released_at"] == second["released_at"]
        assert Decimal(first["final_equity"]) == 1048
        assert (
            Decimal(main["cash_balances"]["USD"]) + Decimal(main["cash_balances"]["USDT"]) == 10048
        )
        assert record["cumulative_pnl"] == 48
        assert not await run_wallets.has_unreleased(conn, owner)


async def test_stopped_funding_uses_historical_quantity_and_replay_is_safe(
    app_with_lifespan: Any,
) -> None:
    owner = uuid4()
    run = await book(owner, mode="perp")
    start = datetime.now(UTC)
    await fill(owner, run, "SELL", "2", "100", ts=start)
    settlement = start + timedelta(seconds=1)
    await fill(owner, run, "BUY", "1", "110", ts=start + timedelta(seconds=2))
    async with get_conn() as conn:
        await strategy_runs.set_status(conn, run["id"], "stopped")
        before = await run_wallets.get(conn, owner, run["id"])
        item = {"ts": settlement.isoformat(), "funding_rate": 0.001, "mark_price": 100}
        assert await settle_funding(conn, run, item)
    async with get_conn() as conn:
        assert not await settle_funding(conn, run, item)
        after = await run_wallets.get(conn, owner, run["id"])
        assert Decimal(after["cash_balances"]["USDT"]) - Decimal(
            before["cash_balances"]["USDT"]
        ) == Decimal("0.2")
        main = await accounts.get(conn, owner)
        assert (
            main["cash_balances"] == {"USD": "9000.0"}
            or Decimal(main["cash_balances"]["USD"]) == 9000
        )


async def test_stale_price_retains_last_trusted_valuation(
    app_with_lifespan: Any, monkeypatch: Any
) -> None:
    owner = uuid4()
    run = await book(owner)
    await fill(owner, run, "BUY", "1", "100")

    async def stale(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"price": 9999, "is_stale": True}

    monkeypatch.setattr(DataClient, "get_ticker", stale)
    async with get_conn() as conn:
        before = await run_wallets.get(conn, owner, run["id"])
        result = await snapshot(conn, run, get_paper_settings(), None)
        assert result["wallet"]["equity"] == before["last_equity"]
        assert result["wallet"]["valuation_at"] == before["valuation_at"]
        assert result["wallet"]["warnings"]


async def test_overview_conserves_transfers_reset_blocks_and_foreign_wallet_is_hidden(
    client: Any, app_with_lifespan: Any
) -> None:
    from .conftest import make_test_token

    owner = uuid4()
    run = await book(owner)
    headers = {"Authorization": "Bearer " + make_test_token(str(owner))}
    summary = client.get("/accounts/me", headers=headers)
    assert summary.status_code == 200, summary.json()
    assert summary.json()["main_equity"] == 9000
    assert summary.json()["run_wallets_equity"] == 1000
    assert summary.json()["total_equity"] == 10000
    assert summary.json()["net_external_flows"] == 0
    assert client.post("/accounts/me/reset", headers=headers, json={}).status_code == 409
    foreign = {"Authorization": "Bearer " + make_test_token(str(uuid4()))}
    for suffix in ["wallet", "positions"]:
        assert (
            client.get(f"/strategy_runs/{run['id']}/{suffix}", headers=foreign).status_code == 404
        )
    assert (
        client.post(f"/strategy_runs/{run['id']}/release_capital", headers=foreign).status_code
        == 404
    )
    async with get_conn() as conn:
        await strategy_runs.set_status(conn, run["id"], "stopped")
    for _ in range(2):
        response = client.post(f"/strategy_runs/{run['id']}/release_capital", headers=headers)
        assert response.status_code == 200, response.json()
    after = client.get("/accounts/me", headers=headers).json()
    assert after["total_equity"] == 10000
    assert after["run_wallets_equity"] == 0
    assert after["net_external_flows"] == 0


async def test_funding_balance_and_event_roll_back_together(app_with_lifespan: Any) -> None:
    owner = uuid4()
    run = await book(owner, mode="perp")
    start = datetime.now(UTC)
    await fill(owner, run, "BUY", "1", "100", ts=start)
    item = {
        "ts": (start + timedelta(seconds=1)).isoformat(),
        "mark_price": 100,
        "funding_rate": 0.01,
    }
    async with get_conn() as conn:
        before = await run_wallets.get(conn, owner, run["id"])
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                assert await settle_funding(conn, run, item)
                raise RuntimeError("Simulated crash before commit")
        assert await settle_funding(conn, run, item)
        after = await run_wallets.get(conn, owner, run["id"])
        assert (
            Decimal(after["cash_balances"]["USDT"]) - Decimal(before["cash_balances"]["USDT"]) == -1
        )
        assert not await settle_funding(conn, run, item)


async def test_missing_fx_preserves_valuation_and_refuses_capital_release(
    app_with_lifespan: Any, monkeypatch: Any
) -> None:
    owner = uuid4()
    run = await book(owner)
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE strategy_run_wallets SET cash_balances='{"
            + '"EUR":"500"'
            + "}',quote_currency='EUR' WHERE run_id=%s",
            (run["id"],),
        )
        await strategy_runs.set_status(conn, run["id"], "stopped")
        result = await snapshot(conn, run, get_paper_settings(), None)
        assert result["wallet"]["equity"] == 1000
        assert result["wallet"]["warnings"]
        with pytest.raises(run_wallets.WalletConflict):
            async with conn.transaction():
                await run_wallets.release(conn, run, BaseCurrencyConverter("USD", None))
        row = await run_wallets.get(conn, owner, run["id"])
        assert row["released_at"] is None


async def test_concurrent_fill_cannot_overwrite_equity_with_mixed_snapshot(
    app_with_lifespan: Any, monkeypatch: Any
) -> None:
    owner = uuid4()
    run = await book(owner)
    await fill(owner, run, "BUY", "1", "100")

    async def changing_price(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await fill(owner, run, "SELL", "1", "150")
        return {"price": 150, "is_stale": False}

    monkeypatch.setattr(DataClient, "get_ticker", changing_price)
    async with get_conn() as conn:
        result = await snapshot(conn, run, get_paper_settings(), None)
        assert result["wallet"]["warnings"]
        assert result["wallet"]["equity"] == 1000
        current = await run_wallets.get(conn, owner, run["id"])
        assert Decimal(current["cash_balances"]["USDT"]) == 1048
        record = await strategy_runs.get(conn, run["id"])
        assert record["cumulative_pnl"] == 0


async def test_concurrent_release_has_one_capital_event_per_currency(
    app_with_lifespan: Any,
) -> None:
    owner = uuid4()
    run = await book(owner)
    async with get_conn() as conn:
        await strategy_runs.set_status(conn, run["id"], "stopped")

    async def release() -> dict[str, Any]:
        async with get_conn() as conn:
            return await run_wallets.release(conn, run, BaseCurrencyConverter("USD", None))

    first, second = await asyncio.gather(release(), release())
    assert first["released_at"] == second["released_at"]
    async with get_conn() as conn:
        rows = await (
            await conn.execute(
                "SELECT currency,SUM(amount) amount FROM paper_cash_events WHERE run_id=%s GROUP BY currency",
                (run["id"],),
            )
        ).fetchall()
        assert rows[0]["amount"] == 0
        main = await accounts.get(conn, owner)
        assert sum(Decimal(str(v)) for v in main["cash_balances"].values()) == 10000


@pytest.mark.parametrize(
    "closing_qty,expected_qty,expected_loss",
    [("0.5", "0.5", "-10"), ("1", "0", "-20"), ("2", "-1", "-20")],
)
async def test_perp_gap_clamps_position_cash_and_closed_trade_together(
    app_with_lifespan: Any, closing_qty: str, expected_qty: str, expected_loss: str
) -> None:
    owner = uuid4()
    run = await book(owner, mode="perp")
    await fill(owner, run, "BUY", "1", "100", fee="0")
    await fill(owner, run, "SELL", closing_qty, "50", fee="2")
    async with get_conn() as conn:
        wallet = await run_wallets.get(conn, owner, run["id"])
        position = await positions.get(
            conn, account_id=owner, venue="binance", symbol=run["symbol"], run_id=run["id"]
        )
        closed = await (
            await conn.execute(
                "SELECT close_profit_abs FROM closed_trades WHERE run_id=%s", (run["id"],)
            )
        ).fetchone()
        assert (
            Decimal(wallet["cash_balances"]["USDT"]) == Decimal(1000) + Decimal(expected_loss) - 2
        )
        assert position["quantity"] == Decimal(expected_qty)
        assert position["realized_pnl"] == Decimal(expected_loss)
        assert Decimal(str(closed["close_profit_abs"])) == Decimal(expected_loss)


async def test_late_published_funding_is_charged_after_cursor_and_only_once(
    app_with_lifespan: Any, monkeypatch: Any
) -> None:
    from inalpha_paper import wallet_accounting

    owner = uuid4()
    run = await book(owner, mode="perp")
    start = datetime.now(UTC) - timedelta(hours=1)
    async with get_conn() as conn:
        await conn.execute(
            "UPDATE strategy_run_wallets SET created_at=%s,funding_through=%s WHERE run_id=%s",
            (start, start, run["id"]),
        )
    await fill(owner, run, "BUY", "1", "100", fee="0", ts=start + timedelta(minutes=1))
    settlement = start + timedelta(minutes=10)
    responses = [[], [{"ts": settlement.isoformat(), "funding_rate": "0.01", "mark_price": "100"}]]

    async def history(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return (
            responses.pop(0)
            if responses
            else [{"ts": settlement.isoformat(), "funding_rate": "0.01", "mark_price": "100"}]
        )

    async def no_snapshot(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(DataClient, "get_perp_funding_history", history)
    monkeypatch.setattr(wallet_accounting, "snapshot", no_snapshot)
    worker = wallet_accounting.FundingWorker(get_paper_settings())
    await worker.tick()
    await worker.tick()
    await worker.tick()
    async with get_conn() as conn:
        wallet = await run_wallets.get(conn, owner, run["id"])
        assert Decimal(wallet["cash_balances"]["USDT"]) == Decimal(999)
        count = await (
            await conn.execute(
                "SELECT COUNT(*) n FROM paper_cash_events WHERE run_id=%s AND kind='funding'",
                (run["id"],),
            )
        ).fetchone()
        assert count["n"] == 1
