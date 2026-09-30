"""Wallet equity and idempotent funding, independent of strategy lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import jwt
from inalpha_shared.db import get_conn

from .config import PaperSettings
from .data_client import DataClient
from .fx import BaseCurrencyConverter, convert_cash_balances
from .storage import run_wallets

_logger = logging.getLogger(__name__)


def service_token(settings: PaperSettings, account_id: Any) -> str:
    """Bind data reads to the wallet owner, never to a wallet ID."""
    return jwt.encode(
        {"sub": str(account_id), "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.jwt_secret,
        algorithm="HS256",
    )


async def snapshot(
    conn: Any, run: dict[str, Any], settings: PaperSettings, token: str | None
) -> dict[str, Any]:
    """Preserve the last trusted equity whenever prices or FX are unverifiable."""
    # One MVCC statement prevents mixing pre-fill cash with post-fill positions.
    wallet = await (
        await conn.execute(
            "SELECT w.*, COALESCE((SELECT jsonb_agg(p) FROM positions p WHERE p.run_id=w.run_id AND p.account_id=w.account_id AND p.quantity<>0),'[]') book_positions FROM strategy_run_wallets w WHERE w.account_id=%s AND w.run_id=%s",
            (run["account_id"], run["id"]),
        )
    ).fetchone()
    if wallet is None:
        return {"wallet": None, "positions": [], "accounting_status": run["accounting_status"]}
    rows = wallet.pop("book_positions")
    warnings: list[str] = [wallet["funding_warning"]] if wallet.get("funding_warning") else []
    quote = wallet["quote_currency"]
    unrealized = Decimal(0)
    async with DataClient(
        settings.data_service_url, token or service_token(settings, run["account_id"])
    ) as dc:
        converter = BaseCurrencyConverter(wallet["base_currency"], dc)
        equity: Decimal | None = await convert_cash_balances(
            converter, {k: Decimal(str(v)) for k, v in wallet["cash_balances"].items()}
        )
        for p in rows:
            try:
                ticker = await dc.get_ticker(venue=p["venue"], symbol=p["symbol"], fresh=False)
                mark = Decimal(str(ticker["price"]))
                if ticker.get("is_stale") or not mark.is_finite() or mark <= 0:
                    raise ValueError("Stale or invalid price")
                qty, avg = Decimal(str(p["quantity"])), Decimal(str(p["avg_open_price"]))
                unrealized += (mark - avg) * qty
                value = (mark - avg) * qty if run["trading_mode"] == "perp" else mark * qty
                converted = await converter.convert(value, p.get("currency") or quote)
                if converted is not None:
                    assert equity is not None
                    equity += converted
            except Exception:
                warnings.append(
                    f"{p['venue']}/{p['symbol']}: price unavailable or stale; retaining last trusted valuation"
                )
        warnings.extend(converter.warnings)
    if wallet["released_at"] is not None:
        equity = Decimal(str(wallet["final_equity"]))
    elif warnings:
        equity = Decimal(str(wallet["last_equity"])) if wallet["last_equity"] is not None else None
    else:
        updated = await conn.execute(
            "UPDATE strategy_run_wallets SET last_equity=%s,valuation_at=NOW(),valuation_warnings='[]' WHERE run_id=%s AND released_at IS NULL AND revision=%s RETURNING valuation_at",
            (equity, run["id"], wallet["revision"]),
        )
        saved = await updated.fetchone()
        if saved is None:
            warnings.append("Wallet changed during valuation; retaining previous snapshot")
            equity = (
                Decimal(str(wallet["last_equity"])) if wallet["last_equity"] is not None else None
            )
        else:
            assert equity is not None
            await conn.execute(
                "UPDATE strategy_runs SET cumulative_pnl=%s WHERE id=%s",
                (equity - Decimal(wallet["initial_cash"]), run["id"]),
            )
            wallet["valuation_at"] = saved["valuation_at"]
    if warnings:
        await conn.execute(
            "UPDATE strategy_run_wallets SET valuation_warnings=%s::jsonb WHERE run_id=%s",
            (json.dumps(warnings), run["id"]),
        )
    totals = await (
        await conn.execute(
            "SELECT COALESCE((SELECT SUM(fee) FROM orders WHERE run_id=%s AND status='FILLED'),0) fees, COALESCE((SELECT SUM(close_profit_abs) FROM closed_trades WHERE run_id=%s),0) realized, COALESCE((SELECT SUM(amount) FROM paper_cash_events WHERE run_id=%s AND kind='funding'),0) funding",
            (run["id"], run["id"], run["id"]),
        )
    ).fetchone()
    return {
        "wallet": {
            **wallet,
            "equity": equity,
            "net_pnl": equity - Decimal(wallet["initial_cash"]) if equity is not None else None,
            "warnings": warnings,
        },
        "positions": rows,
        "accounting_status": run["accounting_status"],
        "pnl_components": {
            **totals,
            "unrealized": unrealized if not warnings else None,
            "currency": quote,
        },
    }


async def settle_funding(conn: Any, run: dict[str, Any], item: dict[str, Any]) -> bool:
    """Settle a published historical rate against actual quantity at that instant."""
    wallet = await run_wallets.lock(conn, run)
    ts = datetime.fromisoformat(str(item["ts"]).replace("Z", "+00:00"))
    if ts <= wallet["created_at"]:
        return False
    rate, mark = Decimal(str(item["funding_rate"])), Decimal(str(item["mark_price"]))
    if not rate.is_finite() or not mark.is_finite() or mark <= 0:
        raise ValueError("Invalid historical funding rate or mark")
    # Quantity at settlement comes from this book's executed orders, not today's position.
    row = await (
        await conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN filled_quantity ELSE -filled_quantity END),0) qty FROM orders WHERE run_id=%s AND account_id=%s AND status='FILLED' AND ts_event<%s",
            (run["id"], run["account_id"], ts),
        )
    ).fetchone()
    delta = -Decimal(row["qty"]) * rate * mark
    inserted = await run_wallets.event(
        conn,
        run["account_id"],
        run["id"],
        f"funding:{run['id']}:{run['symbol']}:{ts.isoformat()}",
        "funding",
        wallet["quote_currency"],
        delta,
    )
    if inserted:
        await run_wallets.apply_delta(
            conn, run["account_id"], run["id"], delta, currency=wallet["quote_currency"]
        )
    await conn.execute(
        "UPDATE strategy_run_wallets SET funding_through=GREATEST(funding_through,%s) WHERE run_id=%s",
        (ts, run["id"]),
    )
    return inserted


class FundingWorker:
    """Continue charging open stopped perpetual books, with replay-safe events."""

    def __init__(self, settings: PaperSettings) -> None:
        self.settings = settings
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.task = asyncio.create_task(self.loop())

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def tick(self) -> None:
        async with get_conn() as conn:
            rows: list[dict[str, Any]] = cast(
                list[dict[str, Any]],
                await (
                    await conn.execute(
                        "SELECT r.*,w.funding_through,w.created_at wallet_created_at FROM strategy_runs r JOIN strategy_run_wallets w ON w.run_id=r.id WHERE r.trading_mode='perp' AND w.released_at IS NULL"
                    )
                ).fetchall(),
            )
        for run in rows:
            try:
                # 重查本钱包完整历史，迟到发布的记录依赖事件键去重而非游标丢弃。
                start = run["wallet_created_at"]
                end = datetime.now(UTC) - timedelta(minutes=5)
                if end <= start:
                    continue
                token = service_token(self.settings, run["account_id"])
                async with DataClient(self.settings.data_service_url, token) as dc:
                    history = []
                    page_start = start
                    while page_start < end:
                        page_end = min(end, page_start + timedelta(days=7))
                        history.extend(
                            await dc.get_perp_funding_history(
                                venue=run["venue"],
                                symbol=run["symbol"],
                                from_ts=page_start,
                                to_ts=page_end,
                            )
                        )
                        page_start = page_end
                async with get_conn() as conn:
                    history.sort(key=lambda item: str(item["ts"]))
                    for item in history:
                        settlement = datetime.fromisoformat(str(item["ts"]).replace("Z", "+00:00"))
                        if not start < settlement <= end:
                            raise ValueError("Funding settlement outside requested interval")
                        await settle_funding(conn, run, item)
                    await run_wallets.lock(conn, run)
                    await conn.execute(
                        "UPDATE strategy_run_wallets SET funding_through=GREATEST(funding_through,%s),funding_warning=NULL WHERE run_id=%s",
                        (end, run["id"]),
                    )
                async with get_conn() as conn:
                    await snapshot(conn, run, self.settings, token)
            except Exception:
                async with get_conn() as conn:
                    await conn.execute(
                        "UPDATE strategy_run_wallets SET funding_warning=%s WHERE run_id=%s",
                        (
                            "Funding settlement pending: published history unavailable; fees will be retried",
                            run["id"],
                        ),
                    )
                _logger.warning(
                    "Funding pending for run %s; will retry without advancing settlement",
                    run["id"],
                    exc_info=True,
                )

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                _logger.exception("Funding sweep failed")
            await asyncio.sleep(60)
