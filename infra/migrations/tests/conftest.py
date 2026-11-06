"""Dedicated PostgreSQL fixtures for real Alembic migration tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

_DEFAULT_URL = (
    "postgresql+psycopg://quant:devpass@localhost:5433/"
    "inalpha_migration_test"
)


def _psycopg_url(url: str) -> str:
    """Convert SQLAlchemy's explicit psycopg URL into a libpq URL."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _database_name(url: str) -> str:
    return urlsplit(url).path.removeprefix("/")


def _assert_dedicated_test_database(url: str) -> None:
    """Reject every database except the migration suite's dedicated *_test DB."""
    database = _database_name(url)
    if not database.startswith("inalpha_migration_") or not database.endswith("_test"):
        raise pytest.UsageError(
            "INALPHA_MIGRATION_TEST_DATABASE_URL must name "
            "inalpha_migration_*_test; development/production databases are forbidden"
        )


def _provision_test_database(url: str) -> None:
    """Create only the guarded test database through PostgreSQL's maintenance DB."""
    parsed = urlsplit(_psycopg_url(url))
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise pytest.UsageError("automatic test database creation is localhost-only")
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    database = _database_name(url)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (database,)
        ).fetchone()
        if exists is None:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))


@pytest.fixture
def migration_db_url() -> Iterator[str]:
    """Reset only an explicitly dedicated migration test database."""
    configured_url = os.environ.get("INALPHA_MIGRATION_TEST_DATABASE_URL")
    url = configured_url or _DEFAULT_URL
    _assert_dedicated_test_database(url)
    try:
        if configured_url is None:
            _provision_test_database(url)
        connection = psycopg.connect(_psycopg_url(url), autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"dedicated migration test database is unavailable: {exc}")

    with connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    yield url
    with psycopg.connect(_psycopg_url(url), autocommit=True) as cleanup:
        cleanup.execute("DROP SCHEMA public CASCADE")
        cleanup.execute("CREATE SCHEMA public")
