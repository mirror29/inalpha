"""0045 E2 approval-operation migration integration tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from migration_0038_support import alembic, db_url

pytestmark = pytest.mark.integration


def test_0045_repairs_a_legacy_0044_database_without_the_operation_ledger(
    migration_db_url: str,
) -> None:
    """A previously stamped 0044 database must remain deployable after #167."""
    alembic(migration_db_url, "upgrade", "0044")
    with psycopg.connect(db_url(migration_db_url), autocommit=True) as conn:
        conn.execute("DROP TABLE evolution_approval_operations")

    alembic(migration_db_url, "upgrade", "0045")

    expires = datetime.now(UTC) + timedelta(hours=1)
    with psycopg.connect(db_url(migration_db_url), autocommit=True) as conn:
        conn.execute(
            """INSERT INTO evolution_approval_operations
            (operation_id,auth_sub,session_id,tool_name,input_digest,expires_at)
            VALUES ('50000000-0000-4000-8000-000000000001',
                    'user:alice','thread-1','evolver.run_event_campaign',%s,%s)""",
            ("a" * 64, expires),
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM evolution_approval_operations"
        ).fetchone() == (1,)
