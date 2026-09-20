"""0053 Paper Forward fencing migration tests."""

from __future__ import annotations

import psycopg
import pytest
from migration_0038_support import alembic, db_url

pytestmark = pytest.mark.integration


def test_0053_requires_complete_forward_lease_tuple(migration_db_url: str) -> None:
    alembic(migration_db_url, "upgrade", "0053")
    with psycopg.connect(db_url(migration_db_url), autocommit=True) as conn:
        columns = {
            row[0]
            for row in conn.execute(
                """SELECT column_name FROM information_schema.columns
WHERE table_name='paper_evolution_forward_sandboxes'"""
            ).fetchall()
        }
        assert {"lease_owner", "lease_token", "lease_expires_at"} <= columns
        constraints = {
            row[0]
            for row in conn.execute(
                """SELECT conname FROM pg_constraint
WHERE conrelid='paper_evolution_forward_sandboxes'::regclass"""
            ).fetchall()
        }
        assert "ck_paper_forward_lease_tuple" in constraints
