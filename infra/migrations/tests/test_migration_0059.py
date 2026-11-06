"""Proposal diagnostics follow deployed wallets without rewriting balances or receipts."""

from uuid import uuid4

import psycopg
import pytest
from migration_0038_support import alembic, db_url


def test_0059_preserves_wallets_and_immutable_receipts(migration_db_url: str) -> None:
    alembic(migration_db_url, "upgrade", "0058")
    owner, candidate, run, snapshot, campaign = [uuid4() for _ in range(5)]
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute(
            "INSERT INTO accounts(account_id,initial_cash,cash_balances,base_currency) VALUES (%s,10000,'{\"USD\":9000}','USD')",
            (owner,),
        )
        conn.execute(
            "INSERT INTO strategy_candidates(id,code,code_hash,status) VALUES (%s,'migration fixture',%s,'promoted')",
            (candidate, str(candidate)),
        )
        conn.execute(
            "INSERT INTO strategy_runs(id,candidate_id,account_id,status,venue,symbol,timeframe,accounting_status) VALUES (%s,%s,%s,'stopped','binance','BTC/USDT','1h','verified')",
            (run, candidate, owner),
        )
        conn.execute(
            "INSERT INTO strategy_run_wallets(run_id,account_id,base_currency,initial_cash,quote_currency,initial_quote_cash,cash_balances) VALUES (%s,%s,'USD',1000,'USDT',1000,'{\"USDT\":1000}')",
            (run, owner),
        )
        conn.execute(
            "INSERT INTO market_event_snapshots(snapshot_id,cutoff,policy_version,query_hash,events_sha256,fact_count) VALUES (%s,NOW(),'test',%s,%s,0)",
            (snapshot, "a" * 64, "a" * 64),
        )
        conn.execute(
            "INSERT INTO evolution_campaigns(campaign_id,owner_account_id,requested_by_sub,idempotency_key,request_hash,event_snapshot_id,frozen_config,llm_snapshot,llm_config_digest) VALUES (%s,%s,'test',%s,%s,%s,'{}','{}',%s)",
            (campaign, owner, str(campaign), "a" * 64, snapshot, "a" * 64),
        )
        conn.execute(
            "INSERT INTO evolution_proposal_checkpoints(campaign_id,generation,content_sha256,cost_usd,fallback_calls) VALUES (%s,1,%s,0.1,0)",
            (campaign, "b" * 64),
        )
        receipt = conn.execute(
            "SELECT content_sha256,cost_usd,committed_at FROM evolution_proposal_checkpoints WHERE campaign_id=%s",
            (campaign,),
        ).fetchone()
        wallet = conn.execute(
            "SELECT * FROM strategy_run_wallets WHERE run_id=%s", (run,)
        ).fetchone()
    alembic(migration_db_url, "upgrade", "0059")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0059",
        )
        assert (
            conn.execute(
                "SELECT content_sha256,cost_usd,committed_at FROM evolution_proposal_checkpoints WHERE campaign_id=%s",
                (campaign,),
            ).fetchone()
            == receipt
        )
        assert conn.execute(
            "SELECT diagnostics FROM evolution_proposal_checkpoints WHERE campaign_id=%s",
            (campaign,),
        ).fetchone() == ([],)
        assert (
            conn.execute(
                "SELECT * FROM strategy_run_wallets WHERE run_id=%s", (run,)
            ).fetchone()
            == wallet
        )
    # Empty diagnostic defaults can roll back without touching deployed wallets.
    alembic(migration_db_url, "downgrade", "0058")
    alembic(migration_db_url, "upgrade", "0059")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO evolution_proposal_checkpoints(campaign_id,generation,content_sha256,cost_usd,fallback_calls,diagnostics) VALUES (%s,2,%s,0.1,1,'{}')",
                (campaign, "c" * 64),
            )
        conn.execute(
            'INSERT INTO evolution_proposal_checkpoints(campaign_id,generation,content_sha256,cost_usd,fallback_calls,diagnostics) VALUES (%s,2,%s,0.1,1,\'[{"code":"provider_timeout"}]\')',
            (campaign, "c" * 64),
        )
    result = alembic(migration_db_url, "downgrade", "0058", check=False)
    assert result.returncode != 0
    assert "cannot downgrade 0059" in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0059",
        )
        assert (
            conn.execute(
                "SELECT * FROM strategy_run_wallets WHERE run_id=%s", (run,)
            ).fetchone()
            == wallet
        )
        assert conn.execute(
            "SELECT cash_balances FROM accounts WHERE account_id=%s", (owner,)
        ).fetchone() == ({"USD": 9000},)
