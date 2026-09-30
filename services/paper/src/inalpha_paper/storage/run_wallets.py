"""Owner-scoped isolated wallets; callers lock account → wallet → positions."""

from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from inalpha_shared.errors import InalphaError

from . import accounts


class WalletConflict(InalphaError):
    code = "RUN_WALLET_CONFLICT"
    status_code = 409


async def get(
    conn: Any, account_id: UUID, run_id: UUID, *, for_update: bool = False
) -> dict[str, Any] | None:
    """Read a book only through its owner's identity."""
    cur = await conn.execute(
        "SELECT * FROM strategy_run_wallets WHERE account_id=%s AND run_id=%s"
        + (" FOR UPDATE" if for_update else ""),
        (account_id, run_id),
    )
    return cast(dict[str, Any] | None, await cur.fetchone())


async def create(
    conn: Any,
    account_id: UUID,
    run_id: UUID,
    amount: Decimal,
    *,
    quote_currency: str,
    quote_rate: Decimal,
    converter: Any = None,
) -> dict[str, Any]:
    """Atomically reserve actual base-currency cash for one new book."""
    acct = await accounts.get_or_create(conn, account_id, for_update=True)
    currency = acct["base_currency"]
    if not quote_rate.is_finite() or quote_rate <= 0:
        raise WalletConflict("Verified conversion rate required")
    quote_amount = amount / quote_rate
    from ..fx import BaseCurrencyConverter

    converter = converter or BaseCurrencyConverter(currency, None)
    available = await available_cash(conn, acct, converter)
    if not amount.is_finite() or amount <= 0 or amount > available:
        raise WalletConflict("Insufficient main-account available cash for allocation")
    cur = await conn.execute(
        "INSERT INTO strategy_run_wallets(run_id,account_id,base_currency,initial_cash,quote_currency,initial_quote_cash,cash_balances) "
        "VALUES (%s,%s,%s,%s,%s,%s,jsonb_build_object(%s::text,%s::text)) RETURNING *",
        (
            run_id,
            account_id,
            currency,
            amount,
            quote_currency,
            quote_amount,
            quote_currency,
            str(quote_amount),
        ),
    )
    wallet = cast(dict[str, Any] | None, await cur.fetchone())
    if wallet is None:
        raise RuntimeError("Wallet insert returned no row")
    await conn.execute(
        "UPDATE strategy_run_wallets SET last_equity=%s,valuation_at=NOW() WHERE run_id=%s",
        (amount, run_id),
    )
    wallet["last_equity"] = amount
    remaining = amount
    for source in sorted(acct["cash_balances"], key=lambda c: c != currency):
        balance = Decimal(str(acct["cash_balances"][source]))
        if balance <= 0 or remaining <= 0:
            continue
        rate = await converter.rate(source)
        if rate is None or converter.warnings:
            raise WalletConflict("Verified conversion rate required")
        debit = min(balance, remaining / rate)
        await accounts.apply_cash_delta(conn, account_id, -debit, currency=source)
        await event(
            conn, account_id, None, f"capital:main:{run_id}:{source}", "capital_out", source, -debit
        )
        remaining -= debit * rate
    if remaining > Decimal("0.000000001"):
        raise WalletConflict("Insufficient main-account cash")
    await event(
        conn, account_id, run_id, f"capital:{run_id}", "capital_in", quote_currency, quote_amount
    )
    await conn.execute(
        "UPDATE strategy_runs SET accounting_status='verified' WHERE id=%s AND account_id=%s",
        (run_id, account_id),
    )
    return wallet


async def event(
    conn: Any,
    account_id: UUID,
    run_id: UUID | None,
    key: str,
    kind: str,
    currency: str,
    amount: Decimal,
) -> bool:
    """Insert exactly once; false means a previously committed event."""
    cur = await conn.execute(
        "INSERT INTO paper_cash_events(account_id,run_id,event_key,kind,currency,amount) "
        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING RETURNING id",
        (account_id, run_id, key, kind, currency, amount),
    )
    return await cur.fetchone() is not None


async def apply_delta(
    conn: Any, account_id: UUID, run_id: UUID, delta: Decimal, *, currency: str
) -> Decimal:
    """Apply a delta only to an unreleased, owner-bound book."""
    cur = await conn.execute(
        "UPDATE strategy_run_wallets SET revision=revision+1,cash_balances=jsonb_set(cash_balances,ARRAY[%s::text],"
        "to_jsonb((COALESCE(cash_balances->>%s,'0')::numeric+%s)::text),true) "
        "WHERE account_id=%s AND run_id=%s AND released_at IS NULL RETURNING cash_balances",
        (currency, currency, delta, account_id, run_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise WalletConflict("Missing, foreign, or released run wallet")
    return Decimal(row["cash_balances"][currency])


async def list_by_account(conn: Any, account_id: UUID) -> list[dict[str, Any]]:
    """List owner books including finalized snapshots."""
    cur = await conn.execute(
        "SELECT * FROM strategy_run_wallets WHERE account_id=%s ORDER BY created_at DESC",
        (account_id,),
    )
    return list(await cur.fetchall())


async def has_unreleased(conn: Any, account_id: UUID) -> bool:
    """Unreleased books prevent an account reset from erasing allocated capital."""
    cur = await conn.execute(
        "SELECT 1 FROM strategy_run_wallets WHERE account_id=%s AND released_at IS NULL LIMIT 1",
        (account_id,),
    )
    return await cur.fetchone() is not None


async def lock(conn: Any, run: dict[str, Any]) -> dict[str, Any]:
    """Acquire the global lock order and fail closed on legacy or released books."""
    await accounts.get_or_create(conn, run["account_id"], for_update=True)
    wallet = await get(conn, run["account_id"], run["id"], for_update=True)
    if wallet is None or wallet["released_at"] is not None:
        raise WalletConflict("Run has no active isolated wallet; legacy runs cannot resume")
    return wallet


async def require(conn: Any, run: dict[str, Any]) -> dict[str, Any]:
    """Reject fallback to the shared user account."""
    wallet = await get(conn, run["account_id"], run["id"])
    if wallet is None or wallet["released_at"] is not None:
        raise WalletConflict("Run has no active isolated wallet")
    return wallet


async def release(conn: Any, run: dict[str, Any], converter: Any) -> dict[str, Any]:
    """Return capital once, retaining the final performance before transfer."""
    wallet = await get(conn, run["account_id"], run["id"])
    if wallet is None:
        raise WalletConflict("Legacy runs have no releasable wallet")
    await accounts.get_or_create(conn, run["account_id"], for_update=True)
    wallet = await get(conn, run["account_id"], run["id"], for_update=True)
    if wallet is None:
        raise WalletConflict("Run wallet disappeared")
    if wallet["released_at"] is not None:
        return wallet
    current = await (
        await conn.execute(
            "SELECT status FROM strategy_runs WHERE id=%s AND account_id=%s FOR UPDATE",
            (run["id"], run["account_id"]),
        )
    ).fetchone()
    if current["status"] != "stopped":
        raise WalletConflict("Only stopped runs may release capital")
    pos = await (
        await conn.execute(
            "SELECT 1 FROM positions WHERE run_id=%s AND account_id=%s AND quantity<>0 FOR UPDATE",
            (run["id"], run["account_id"]),
        )
    ).fetchone()
    if pos is not None:
        raise WalletConflict("Close all run positions before releasing capital")
    if run["trading_mode"] == "perp":
        latest = await (
            await conn.execute(
                "SELECT MAX(ts_event) ts FROM orders WHERE run_id=%s AND status='FILLED'",
                (run["id"],),
            )
        ).fetchone()
        if latest["ts"] is not None and wallet["funding_through"] < latest["ts"]:
            raise WalletConflict("Funding settlement pending; retry after reconciliation")
    equity = Decimal(0)
    for currency, value in wallet["cash_balances"].items():
        amount = Decimal(str(value))
        converted = await converter.convert(amount, currency)
        if converted is None or converter.warnings:
            raise WalletConflict("Verified conversion rate required for release")
        equity += converted
    for currency, value in wallet["cash_balances"].items():
        amount = Decimal(str(value))
        await event(
            conn,
            run["account_id"],
            run["id"],
            f"release:{run['id']}:{currency}",
            "capital_out",
            currency,
            -amount,
        )
        await event(
            conn,
            run["account_id"],
            None,
            f"release:main:{run['id']}:{currency}",
            "capital_in",
            currency,
            amount,
        )
        await accounts.apply_cash_delta(conn, run["account_id"], amount, currency=currency)
    row = await (
        await conn.execute(
            "UPDATE strategy_run_wallets SET cash_balances='{}',released_at=NOW(),final_equity=%s,last_equity=%s,valuation_at=NOW() WHERE run_id=%s RETURNING *",
            (equity, equity, run["id"]),
        )
    ).fetchone()
    await conn.execute(
        "UPDATE strategy_runs SET cumulative_pnl=%s WHERE id=%s",
        (equity - Decimal(wallet["initial_cash"]), run["id"]),
    )
    return cast(dict[str, Any], row)


async def available_cash(conn: Any, acct: dict[str, Any], converter: Any) -> Decimal:
    """Reserve liabilities and legacy perpetual margin before transferring capital."""
    from ..fx import convert_cash_balances

    cash = await convert_cash_balances(
        converter, {k: Decimal(str(v)) for k, v in acct["cash_balances"].items()}
    )
    rows = await (
        await conn.execute(
            "SELECT currency,SUM(margin_used) amount FROM positions WHERE account_id=%s AND run_id IS NULL GROUP BY currency",
            (acct["account_id"],),
        )
    ).fetchall()
    for row in rows:
        if row["amount"]:
            value = await converter.convert(
                Decimal(str(row["amount"])), row["currency"] or acct["base_currency"]
            )
            if value is not None:
                cash -= value
    if converter.warnings:
        raise WalletConflict("Fresh verified FX required for allocation")
    return cash
