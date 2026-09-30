"""Run durable-loop checks in a fresh local database without purchasing model calls."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parents[3]
TEST_FILES = (
    "test_loop_storage.py",
    "test_loop_dispatch.py",
    "test_loop_handoff.py",
    "test_loop_authorizations.py",
    "test_loop_snapshot.py",
    "test_loop_baseline_resume.py",
    "test_loop_baseline_fencing.py",
    "test_loop_manager.py",
    "test_loop_budget_projection.py",
    "test_loop_start.py",
)


def main() -> int:
    """Create, migrate and remove only this invocation's disposable test database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-five-generations", action="store_true")
    parser.add_argument("--evaluation-concurrency", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    source_url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not source_url:
        parser.error("DATABASE_URL is required")
    parsed = urlsplit(source_url)
    if parsed.scheme not in {"postgresql", "postgresql+psycopg"} or parsed.hostname not in {
        "localhost", "127.0.0.1", "::1",
    }:
        parser.error("validation requires a local PostgreSQL URL")
    database_name = f"inalpha_loop_check_{uuid4().hex}"
    test_url = urlunsplit(parsed._replace(path=f"/{database_name}"))
    env = os.environ.copy()
    env.update({
        "DATABASE_URL": test_url,
        "EVOLVER_TEST_DATABASE_URL": test_url,
        "CANDIDATE_EVALUATION_CONCURRENCY": str(args.evaluation_concurrency),
    })
    for key in (
        "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
    ):
        env[key] = "1"
    logs = ROOT / ".tmp" / "loop-validation" / database_name
    logs.mkdir(parents=True)
    admin_url = source_url.replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        try:
            with (logs / "migrations.log").open("w") as output:
                migration = subprocess.run(
                    ["uv", "run", "alembic", "upgrade", "head"],
                    cwd=ROOT / "infra/migrations", env=env, stdout=output, stderr=subprocess.STDOUT,
                    check=False,
                )
            if migration.returncode:
                print(f"Migration failed. Logs: {logs}")
                return migration.returncode
            tests = list(TEST_FILES)
            if args.include_five_generations:
                tests.append("test_loop_five_generations.py")
            with (logs / "pytest.log").open("w") as output:
                result = subprocess.run(
                    ["uv", "run", "pytest", "-q", f"--junitxml={logs / 'results.xml'}",
                     *(f"tests/{name}" for name in tests)],
                    cwd=ROOT / "services/evolver", env=env,
                    stdout=output, stderr=subprocess.STDOUT, check=False,
                )
            print(f"Validation exit code: {result.returncode}. Logs: {logs}")
            return result.returncode
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name))
            )


if __name__ == "__main__":
    raise SystemExit(main())
