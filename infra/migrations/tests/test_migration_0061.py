"""Legacy receipts stay distinct from zero, and uncertainty cannot be silently dropped."""
from uuid import uuid4

import psycopg
from migration_0038_support import alembic, db_url


def test_0061_preserves_unknown_legacy_cost_and_protects_new_receipts(migration_db_url):
    alembic(migration_db_url, "upgrade", "0060")
    run_id, candidate_id, owner = uuid4(), uuid4(), uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO strategy_evo_runs(run_id,owner_account_id,requested_by_sub,idempotency_key,request_hash,queued_at,seed_strategy_id,budget,config,status)
VALUES(%s,%s,'test',%s,'hash',NOW(),'seed',4,'{}','failed')""", (run_id, owner, str(run_id)))
        conn.execute("""INSERT INTO strategy_evo_candidates(candidate_id,run_id,slot,generation,stage,outcome,status,report,llm_cost_usd)
VALUES(%s,%s,0,1,'completed','mutation_failed','evaluated',NULL,0.0123)""", (candidate_id, run_id))
    alembic(migration_db_url, "upgrade", "0061")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        row = conn.execute("SELECT usage_status,llm_cost_usd FROM strategy_evo_candidates WHERE candidate_id=%s", (candidate_id,)).fetchone()
        assert row[0] == 'legacy_unknown'
        assert float(row[1]) == 0.0123
        conn.execute("UPDATE strategy_evo_candidates SET usage_status='unknown' WHERE candidate_id=%s", (candidate_id,))
    result = alembic(migration_db_url, "downgrade", "0060", check=False)
    assert result.returncode != 0
    assert "cannot downgrade 0061 with recorded usage uncertainty" in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0061",)
