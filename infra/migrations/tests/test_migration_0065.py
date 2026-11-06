"""Retain E2 chat provenance during attempted audit-destroying rollback."""
from uuid import uuid4

import psycopg
from migration_0038_support import alembic, db_url


def test_0065_blocks_association_loss(migration_db_url):
    alembic(migration_db_url, "upgrade", "0065")
    operation = uuid4()
    with psycopg.connect(db_url(migration_db_url)) as conn:
        conn.execute("""INSERT INTO chat_evolution_operations(auth_sub,operation_id,invocation_id,tool_name)
VALUES('alice',%s,%s,'evolver.start_evolution_loop')""", (operation, uuid4()))
    result = alembic(migration_db_url, "downgrade", "0064", check=False)
    assert result.returncode != 0
    assert 'cannot downgrade 0065 with recorded evolution chat associations' in result.stderr
    with psycopg.connect(db_url(migration_db_url)) as conn:
        assert conn.execute('SELECT version_num FROM alembic_version').fetchone() == ('0065',)
        assert conn.execute('SELECT COUNT(*) FROM chat_evolution_operations WHERE operation_id=%s', (operation,)).fetchone() == (1,)
