"""Chat call receipts cannot disappear during rollback."""
from uuid import uuid4

import psycopg
from migration_0038_support import alembic, db_url


def test_0064_retains_unknown_calls_and_blocks_audit_loss(migration_db_url):
    alembic(migration_db_url, "upgrade", "0064")
    call_id = uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO chat_usage_receipts(call_id,invocation_id,auth_sub,step_number,call_source)
VALUES(%s,%s,'alice',0,'chat')""", (call_id, uuid4()))
        assert conn.execute("SELECT usage_status,estimated_cost_usd FROM chat_usage_receipts WHERE call_id=%s", (call_id,)).fetchone() == ('unknown', None)
    result = alembic(migration_db_url, "downgrade", "0063", check=False)
    assert result.returncode != 0
    assert "cannot downgrade 0064 with recorded chat calls" in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ('0064',)
        assert conn.execute("SELECT COUNT(*) FROM chat_usage_receipts WHERE call_id=%s", (call_id,)).fetchone() == (1,)
