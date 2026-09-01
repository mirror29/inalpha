"""0044 migration-head merge integration test."""

from __future__ import annotations

import psycopg
from migration_0038_support import alembic, db_url


def test_0044_restores_a_single_upgrade_head(migration_db_url: str) -> None:
    """Upgrade both 0043 branches through one deterministic head."""
    alembic(migration_db_url, "upgrade", "head")

    current = alembic(migration_db_url, "current")
    assert "0044 (head)" in current.stdout
    assert "0043_waitlist (head)" not in current.stdout

    alembic(migration_db_url, "downgrade", "0042")
    with psycopg.connect(db_url(migration_db_url), autocommit=True) as conn:
        assert conn.execute("SELECT to_regclass('evolution_campaigns')").fetchone() == (
            None,
        )
        assert (
            conn.execute(
                """SELECT 1 FROM information_schema.columns
                WHERE table_name='users' AND column_name='access_status'"""
            ).fetchone()
            is None
        )
