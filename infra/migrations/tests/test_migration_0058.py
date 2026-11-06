"""Run wallet migration preserves history and prevents an unsafe downgrade."""

from __future__ import annotations

from uuid import uuid4

import psycopg
from migration_0038_support import alembic, db_url


def test_0058_pauses_legacy_without_moving_cash_or_positions(
    migration_db_url: str,
) -> None:
    alembic(migration_db_url, "upgrade", "0057")
    owner, candidate, run = uuid4(), uuid4(), uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute(
            "INSERT INTO accounts(account_id,initial_cash,cash_balances,base_currency) VALUES (%s,10000,'{\"USD\":10000}','USD')",
            (owner,),
        )
        conn.execute(
            "INSERT INTO strategy_candidates(id,code,code_hash,status) VALUES (%s,'legacy fixture','wallet-migration','promoted')",
            (candidate,),
        )
        conn.execute(
            "INSERT INTO strategy_runs(id,candidate_id,account_id,status,venue,symbol,timeframe,cumulative_pnl) VALUES (%s,%s,%s,'running','binance','BTC/USDT','1h',2900)",
            (run, candidate, owner),
        )
        conn.execute(
            "INSERT INTO positions(account_id,venue,symbol,quantity,avg_open_price,realized_pnl,generation) VALUES (%s,'binance','BTC/USDT',2,100,0,1)",
            (owner,),
        )
    alembic(migration_db_url, "upgrade", "0058")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute(
            "SELECT status,accounting_status,cumulative_pnl,original_cumulative_pnl FROM strategy_runs WHERE id=%s",
            (run,),
        ).fetchone() == ("stopped", "legacy_unverified", 2900, 2900)
        assert conn.execute(
            "SELECT quantity,run_id FROM positions WHERE account_id=%s", (owner,)
        ).fetchone() == (2, None)
        assert conn.execute(
            "SELECT cash_balances FROM accounts WHERE account_id=%s", (owner,)
        ).fetchone() == ({"USD": 10000},)
        assert conn.execute("SELECT COUNT(*) FROM strategy_run_wallets").fetchone() == (
            0,
        )
        conn.execute(
            "INSERT INTO strategy_run_wallets(run_id,account_id,base_currency,initial_cash,quote_currency,initial_quote_cash,cash_balances) VALUES (%s,%s,'USD',1000,'USDT',1000,'{\"USDT\":1000}')",
            (run, owner),
        )
    result = alembic(migration_db_url, "downgrade", "0057", check=False)
    assert result.returncode != 0
    assert "Isolated wallets exist" in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0058",
        )
        assert conn.execute("SELECT COUNT(*) FROM strategy_run_wallets").fetchone() == (
            1,
        )
