"""Loop consent must survive rollback attempts; unrelated tool names remain prohibited."""
from uuid import uuid4

import psycopg
import pytest
from migration_0038_support import alembic, db_url


def test_0067_preserves_loop_commands_and_rejects_unrelated_tools(migration_db_url):
    alembic(migration_db_url, "upgrade", "0067")
    operation = uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO evolution_approval_operations(operation_id,auth_sub,session_id,tool_name,input_digest,expires_at)
VALUES(%s,'alice','loop','evolver.start_evolution_loop',repeat('a',64),NOW()+INTERVAL '24 hours')""", (operation,))
    result = alembic(migration_db_url, "downgrade", "0066", check=False)
    assert result.returncode != 0
    assert "cannot downgrade 0067 with recorded loop approvals" in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0067",)
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE evolution_approval_operations SET tool_name='paper.start_strategy' WHERE operation_id=%s", (operation,))
        conn.execute("DELETE FROM evolution_approval_operations WHERE operation_id=%s", (operation,))
    alembic(migration_db_url, "downgrade", "0066")
