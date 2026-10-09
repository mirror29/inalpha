"""Retry lineage preserves old records and prevents audit-destructive downgrades."""
from uuid import uuid4

import psycopg
from migration_0038_support import alembic, db_url


def test_0060_preserves_legacy_run_and_retains_retry_audit(migration_db_url):
    alembic(migration_db_url, "upgrade", "0059")
    run_id, retry_id, owner = uuid4(), uuid4(), uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO strategy_evo_runs(run_id,owner_account_id,requested_by_sub,idempotency_key,request_hash,queued_at,seed_strategy_id,budget,config,status,llm_cost_usd)
        VALUES (%s,%s,'test',%s,'original',NOW(),'legacy-seed',4,'{}','failed',0.0123)""", (run_id,owner,str(run_id)))
        before = conn.execute("SELECT seed_strategy_id,budget,config,status,llm_cost_usd FROM strategy_evo_runs WHERE run_id=%s", (run_id,)).fetchone()
    alembic(migration_db_url, "upgrade", "0060")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT experiment_id,retry_of_run_id,attempt_number FROM strategy_evo_runs WHERE run_id=%s", (run_id,)).fetchone() == (run_id, None, 1)
        assert conn.execute("SELECT seed_strategy_id,budget,config,status,llm_cost_usd FROM strategy_evo_runs WHERE run_id=%s", (run_id,)).fetchone() == before
    legacy_after_upgrade = uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO strategy_evo_runs(run_id,owner_account_id,requested_by_sub,idempotency_key,request_hash,queued_at,seed_strategy_id,budget,config,status)
        VALUES (%s,%s,'test',%s,'old-writer',NOW(),'legacy-seed',4,'{}','failed')""", (legacy_after_upgrade,owner,str(legacy_after_upgrade)))
        assert conn.execute("SELECT experiment_id FROM strategy_evo_runs WHERE run_id=%s", (legacy_after_upgrade,)).fetchone() == (legacy_after_upgrade,)
    alembic(migration_db_url, "downgrade", "0059")
    alembic(migration_db_url, "upgrade", "0060")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO strategy_evo_runs(run_id,owner_account_id,requested_by_sub,idempotency_key,request_hash,queued_at,seed_strategy_id,budget,config,status,experiment_id,retry_of_run_id,attempt_number)
        VALUES (%s,%s,'test',%s,'retry',NOW(),'legacy-seed',4,'{}','queued',%s,%s,2)""", (retry_id, owner, str(retry_id), run_id, run_id))
    result = alembic(migration_db_url, "downgrade", "0059", check=False)
    assert result.returncode != 0
    assert "cannot downgrade 0060 with recorded retry lineage" in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0060",)
        assert conn.execute("SELECT retry_of_run_id FROM strategy_evo_runs WHERE run_id=%s", (retry_id,)).fetchone() == (run_id,)
