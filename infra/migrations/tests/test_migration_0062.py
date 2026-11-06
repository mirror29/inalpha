"""Real PostgreSQL round trips preserve tiny costs and reject lossy rollback."""
from decimal import Decimal
from uuid import uuid4

import psycopg
from migration_0038_support import alembic, db_url


def test_0062_preserves_costs_and_blocks_lossy_downgrade(migration_db_url):
    alembic(migration_db_url, "upgrade", "0061")
    run_id, candidate_id, owner = uuid4(), uuid4(), uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO strategy_evo_runs(run_id,owner_account_id,requested_by_sub,idempotency_key,request_hash,queued_at,seed_strategy_id,budget,config,status,llm_cost_usd)
VALUES(%s,%s,'test',%s,'hash',NOW(),'seed',4,'{}','failed',0.0123)""", (run_id, owner, str(run_id)))
        conn.execute("""INSERT INTO strategy_evo_candidates(candidate_id,run_id,slot,generation,stage,outcome,status,report,llm_cost_usd)
VALUES(%s,%s,0,1,'completed','mutation_failed','evaluated',NULL,NULL)""", (candidate_id, run_id))
    alembic(migration_db_url, "upgrade", "0062")
    tiny = Decimal("0.000000123456")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT llm_cost_usd FROM strategy_evo_runs WHERE run_id=%s", (run_id,)).fetchone()[0] == Decimal("0.0123")
        assert conn.execute("SELECT llm_cost_usd FROM strategy_evo_candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()[0] is None
        conn.execute("UPDATE strategy_evo_runs SET llm_cost_usd=%s WHERE run_id=%s", (tiny, run_id))
        conn.execute("UPDATE strategy_evo_candidates SET llm_cost_usd=%s WHERE candidate_id=%s", (tiny, candidate_id))
        for table, key, value in (("strategy_evo_runs", "run_id", run_id), ("strategy_evo_candidates", "candidate_id", candidate_id)):
            assert conn.execute(f"SELECT llm_cost_usd FROM {table} WHERE {key}=%s", (value,)).fetchone()[0] == tiny
    result = alembic(migration_db_url, "downgrade", "0061", check=False)
    assert result.returncode != 0
    assert "cannot downgrade 0062 with recorded high-precision costs" in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0062",)
        conn.execute("UPDATE strategy_evo_runs SET llm_cost_usd=0 WHERE run_id=%s", (run_id,))
    candidate_only = alembic(migration_db_url, "downgrade", "0061", check=False)
    assert candidate_only.returncode != 0
    assert "cannot downgrade 0062 with recorded high-precision costs" in candidate_only.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("UPDATE strategy_evo_candidates SET llm_cost_usd=0 WHERE candidate_id=%s", (candidate_id,))
    alembic(migration_db_url, "downgrade", "0061")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT llm_cost_usd FROM strategy_evo_runs WHERE run_id=%s", (run_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT llm_cost_usd FROM strategy_evo_candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()[0] == 0
