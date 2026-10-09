"""Approved commands survive upgrade and cannot be silently removed by rollback."""
from uuid import uuid4

import psycopg
from migration_0038_support import alembic, db_url


def test_0063_preserves_operation_identity_and_blocks_command_loss(migration_db_url):
    alembic(migration_db_url, "upgrade", "0062")
    operation_id = uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO evolution_approval_operations(operation_id,auth_sub,session_id,tool_name,input_digest,expires_at)
VALUES(%s,'alice','thread','evolver.run_evolution',repeat('a',64),NOW()+INTERVAL '24 hours')""", (operation_id,))
    alembic(migration_db_url, "upgrade", "0063")
    with psycopg.connect(db_url(migration_db_url)) as conn:
        row = conn.execute("SELECT auth_sub,execution_input FROM evolution_approval_operations WHERE operation_id=%s", (operation_id,)).fetchone()
        assert row == ('alice', None)
        conn.execute("UPDATE evolution_approval_operations SET execution_input=%s::jsonb WHERE operation_id=%s", ('{"request":{"seedStrategyId":"seed"},"llm_snapshot":{"config_id":"owned-config"}}', operation_id))
    result = alembic(migration_db_url, "downgrade", "0062", check=False)
    assert result.returncode != 0
    assert "cannot downgrade 0063 with recorded approved commands" in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ('0063',)
        row = conn.execute("SELECT execution_input FROM evolution_approval_operations WHERE operation_id=%s", (operation_id,)).fetchone()
        assert row[0]['llm_snapshot']['config_id'] == 'owned-config'
