"""Run durable-loop checks in a fresh local database without purchasing model calls."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parents[3]
_SAFE_QUERY_OPTIONS = {"sslmode", "connect_timeout", "application_name"}


def local_database_urls(source_url: str, database_name: str) -> tuple[str, str]:
    """Pin libpq and SQLAlchemy to a local address and the generated database path."""
    try:
        parsed = urlsplit(source_url)
        if parsed.scheme not in {"postgresql", "postgresql+psycopg"} or parsed.hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError("validation requires a local PostgreSQL URL")
        options = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        keys = [key for key, _ in options]
        if len(keys) != len(set(keys)) or not set(keys) <= _SAFE_QUERY_OPTIONS:
            raise ValueError(
                "validation rejects connection target overrides and unknown URL options"
            )
        connect_timeout = int(dict(options).get("connect_timeout", "5"))
        if not 1 <= connect_timeout <= 30:
            raise ValueError("connect_timeout must be between 1 and 30 seconds")
        options = [(key, value) for key, value in options if key != "connect_timeout"]
        options.append(("connect_timeout", str(connect_timeout)))
        port = parsed.port or 5432
        if not 1 <= port <= 65535:
            raise ValueError("invalid port")
        credentials = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
        host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
        address = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        query = urlencode([*options, ("hostaddr", address)])
        normalized = parsed._replace(netloc=f"{credentials}{host}:{port}", query=query, fragment="")
        admin_url = urlunsplit(normalized._replace(scheme="postgresql"))
        test_url = urlunsplit(
            normalized._replace(scheme="postgresql+psycopg", path=f"/{database_name}")
        )
    except ValueError as exc:
        raise ValueError(
            "validation requires a local PostgreSQL URL without target overrides"
        ) from exc
    return admin_url, test_url


def discover_tests(test_dir: Path, *, include_five_generations: bool) -> list[str]:
    """Discover durable-loop tests; the expensive five-generation suite stays opt-in."""
    files = sorted(path.name for path in test_dir.glob("test_loop_*.py") if path.is_file())
    if "test_loop_five_generations.py" not in files:
        raise ValueError("No durable-loop five-generation suite found; check the opt-in contract")
    if not include_five_generations:
        files = [name for name in files if name != "test_loop_five_generations.py"]
    if not files:
        raise ValueError("No durable-loop tests found")
    return files


def terminate_validation(signum: int, _frame: object) -> None:
    """Unwind SIGTERM through database cleanup instead of exiting abruptly."""
    raise SystemExit(128 + signum)


def main() -> int:
    """Create, migrate and remove only this invocation's disposable test database."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-five-generations", action="store_true")
    parser.add_argument("--evaluation-concurrency", type=int, choices=range(1, 5), default=2)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, terminate_validation)
    source_url = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not source_url:
        parser.error("DATABASE_URL is required")
    database_name = f"inalpha_loop_check_{uuid4().hex}"
    try:
        admin_url, test_url = local_database_urls(source_url, database_name)
        tests = discover_tests(
            ROOT / "services/evolver/tests", include_five_generations=args.include_five_generations
        )
    except ValueError as exc:
        parser.error(str(exc))
    env = os.environ.copy()
    env.update(
        {
            "DATABASE_URL": test_url,
            "EVOLVER_TEST_DATABASE_URL": test_url,
            "CANDIDATE_EVALUATION_CONCURRENCY": str(args.evaluation_concurrency),
        }
    )
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        env[key] = "1"
    logs = ROOT / ".tmp" / "loop-validation" / database_name
    logs.mkdir(parents=True)
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        try:
            admin.execute("SET statement_timeout=15000")
            with (logs / "migrations.log").open("w") as output:
                migration = subprocess.run(
                    ["uv", "run", "alembic", "upgrade", "head"],
                    cwd=ROOT / "infra/migrations",
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=120,
                )
            if migration.returncode:
                print(f"Migration failed. Logs: {logs}")
                return migration.returncode
            with (logs / "pytest.log").open("w") as output:
                result = subprocess.run(
                    [
                        "uv",
                        "run",
                        "pytest",
                        "-q",
                        f"--junitxml={logs / 'results.xml'}",
                        *(f"tests/{name}" for name in tests),
                    ],
                    cwd=ROOT / "services/evolver",
                    env=env,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    check=False,
                    timeout=600,
                )
            print(f"Validation exit code: {result.returncode}. Logs: {logs}")
            return result.returncode
        except subprocess.TimeoutExpired:
            print(f"Validation timed out. Logs: {logs}")
            return 124
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name))
            )


if __name__ == "__main__":
    raise SystemExit(main())
