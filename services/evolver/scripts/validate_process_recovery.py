"""Verify interrupted E1 reconciliation in a complete isolated Evolver process.

Uses a new migrated local database, synthetic records and unusable credentials.
Preserves the audit database. No production identity or model grant is created.
This is not an E2 authorized-baseline or in-flight model recovery acceptance.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from validate_durable_loop import ROOT, local_database_urls


class RejectExternalCalls(BaseHTTPRequestHandler):
    """Count and reject downstream requests without collecting their contents."""

    calls = 0

    def do_GET(self) -> None:
        type(self).calls += 1
        self.send_error(503)

    do_POST = do_GET

    def log_message(self, *_args: object) -> None:
        pass


def healthy(process: subprocess.Popen, port: int) -> None:
    """Require the real application's lifespan and all dispatchers to start."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("isolated application exited before health")
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                payload = json.load(response)
                if payload.get("status") == "ok" and payload.get("event_evolution") == "enabled":
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise TimeoutError("isolated application health deadline")


async def seed_interrupted(database_url: str) -> str:
    """Persist an orphan E1 run and known/unknown fixture costs after startup."""
    from inalpha_evolver.governor.seed import SEED_STRATEGY_CODE
    from inalpha_evolver.storage import candidates, runs

    owner = uuid4()
    async with await psycopg.AsyncConnection.connect(
        database_url, row_factory=dict_row, connect_timeout=5
    ) as conn:
        await conn.execute("SET LOCAL statement_timeout='5s'")
        run, _ = await runs.insert_run(
            conn,
            owner_account_id=owner,
            requested_by_sub=str(owner),
            idempotency_key=str(uuid4()),
            request_hash="a" * 64,
            seed_strategy_id="sma_cross_v1",
            seed_source=SEED_STRATEGY_CODE,
            seed_hash=hashlib.sha256(SEED_STRATEGY_CODE.encode()).hexdigest(),
            budget=2,
            config={
                "venue": "binance",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "from_ts": "2026-01-01T00:00:00Z",
                "as_of": "2026-02-01T00:00:00Z",
            },
            llm_snapshot={
                "config_digest": "a" * 64,
                "config_id": "isolated-fixture",
                "provider": "fixture",
                "model": "not-a-model",
                "pricing": {},
            },
            llm_credential_grant="unusable-isolated-fixture-not-a-grant-" + "x" * 100,
            queued_at=datetime.now(UTC),
        )
        await runs.transition(conn, run["run_id"], from_statuses=("queued",), to_status="running")
        await candidates.insert_slot(conn, run["run_id"], 0, "completed synthetic slot")
        await candidates.update_slot(
            conn,
            run["run_id"],
            0,
            stage="completed",
            outcome="no_change",
            usage_status="known",
            llm_cost_usd=Decimal("0.0123"),
            input_tokens=11,
            output_tokens=13,
        )
        await candidates.insert_slot(conn, run["run_id"], 1, "interrupted synthetic slot")
        await candidates.update_slot(conn, run["run_id"], 1, usage_status="unknown")
        return str(run["run_id"])


async def bounded_seed(database_url: str) -> str:
    """Bound the frozen-process fixture transaction, including lock and connection waits."""
    async with asyncio.timeout(15):
        return await seed_interrupted(database_url)


def observe(database_url: str, run_id: str) -> dict:
    """Read only status and cost metadata from this invocation's audit database."""
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='5s'")
        run = conn.execute(
            "SELECT status,failure_code FROM strategy_evo_runs WHERE run_id=%s", (run_id,)
        ).fetchone()
        slots = conn.execute(
            "SELECT slot,outcome,error_code,usage_status,llm_cost_usd,input_tokens,output_tokens "
            "FROM strategy_evo_candidates WHERE run_id=%s ORDER BY slot",
            (run_id,),
        ).fetchall()
        loops = conn.execute("SELECT count(*) AS n FROM evolution_loops").fetchone()["n"]
    return {"run": dict(run), "slots": [dict(row) for row in slots], "loops": loops}


@contextmanager
def record_receipt(logs: Path, report: dict):
    """Preserve failed or partial audit evidence without storing raw exceptions."""
    report["result"] = "failed"
    try:
        yield
    except BaseException as exc:
        report["result"] = "failed"
        report["error_type"] = type(exc).__name__
        raise
    finally:
        (logs / "receipt.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
        print(
            json.dumps(
                {
                    "result": report["result"],
                    "database": report.get("database"),
                    "receipt": str(logs / "receipt.json"),
                }
            )
        )


def main() -> int:
    """Create a fresh migration-chain database and restart the unmodified application."""
    if sys.flags.optimize:
        raise RuntimeError("process acceptance refuses optimized Python; assertions must run")
    if os.name != "posix":
        raise RuntimeError("process acceptance requires POSIX signals before any DB side effects")
    source = os.environ.get("DATABASE_URL")
    if not source:
        raise ValueError(
            "local DATABASE_URL must be supplied through the environment; dotenv files must be absent"
        )
    database = f"inalpha_process_audit_{uuid4().hex}"
    admin_url, test_url = local_database_urls(source, database)
    raw_url = test_url.replace("postgresql+psycopg://", "postgresql://")
    logs = ROOT / ".tmp" / "process-recovery" / database
    logs.mkdir(parents=True)
    report = {
        "database": database,
        "scope": "isolated complete Evolver application; synthetic E1 records",
        "e2_authorized_baseline_recovery": "not tested",
        "production_recovery": "not tested",
        "fixture_known_cost_usd": "0.0123",
        "provider_credentials": "none; unusable fixture only",
    }
    with record_receipt(logs, report):
        report["checkout_head"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        runtime_paths = ("services/evolver/src", "services/paper/src", "services/_shared")
        report["runtime_source_trees"] = {}
        for path in runtime_paths:
            report["runtime_source_trees"][path] = subprocess.check_output(
                ["git", "rev-parse", f"HEAD:{path}"], cwd=ROOT, text=True
            ).strip()
            status = subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=all", "--", path],
                cwd=ROOT,
                text=True,
            )
            if status.strip():
                raise RuntimeError("runtime source has tracked or untracked changes")
        # Settings may consult checkout/cwd dotenv files; refuse these instead of modifying settings.
        for path in (ROOT / ".env", ROOT / "services/evolver/.env", ROOT / "infra/.env"):
            if path.exists():
                raise RuntimeError("isolated validation requires a checkout without dotenv files")
        report["dotenv_files_absent"] = True
        report["runtime_source_clean"] = True
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        report["audit_database_retained"] = True
        # Only operating-system settings are inherited; credentials and app configuration are not.
        env = {
            key: os.environ[key]
            for key in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")
            if key in os.environ
        }
        env.update(
            DATABASE_URL=test_url,
            EVENT_EVOLUTION_ENABLED="true",
            WEB_CONCURRENCY="1",
            JWT_SECRET="isolated-process-fixture-only",
            EVOLUTION_CREDENTIAL_PUBLIC_KEY_B64="",
        )
        with (logs / "migrations.log").open("w") as output:
            subprocess.run(
                [
                    str(ROOT / "infra/migrations/.venv/bin/python"),
                    "-m",
                    "alembic",
                    "upgrade",
                    "head",
                ],
                cwd=ROOT / "infra/migrations",
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=120,
                check=True,
            )
        with psycopg.connect(raw_url) as conn:
            report["schema"] = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if report["schema"] != "0068":
            raise RuntimeError("unexpected migration head; audit assumptions require review")
        sink = ThreadingHTTPServer(("127.0.0.1", 0), RejectExternalCalls)
        threading.Thread(target=sink.serve_forever, daemon=True).start()
        for key in (
            "DATA_SERVICE_URL",
            "PAPER_SERVICE_URL",
            "DASHBOARD_SERVICE_URL",
            "ORCHESTRATION_SERVICE_URL",
            "LLM_BASE_URL",
        ):
            env[key] = f"http://127.0.0.1:{sink.server_port}"
        process = None
        stopped = False
        try:
            with (logs / "application.log").open("w") as output:

                def launch() -> tuple[subprocess.Popen, int]:
                    # Pass the bound listener to uvicorn; no close-and-rebind allocation race.
                    with socket.socket() as listener:
                        listener.bind(("127.0.0.1", 0))
                        listener.listen(128)
                        port = int(listener.getsockname()[1])
                        child = subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "uvicorn",
                                "inalpha_evolver.main:app",
                                "--fd",
                                str(listener.fileno()),
                            ],
                            pass_fds=(listener.fileno(),),
                            cwd=ROOT / "services/evolver",
                            env=env,
                            stdout=output,
                            stderr=subprocess.STDOUT,
                        )
                    return child, port

                process, port = launch()
                healthy(process, port)
                # Freeze the owned process before injecting the orphan record: no fixture can dispatch.
                process.send_signal(signal.SIGSTOP)
                stopped = True
                run_id = asyncio.run(bounded_seed(raw_url))
                before = observe(raw_url, run_id)
                assert before["run"]["status"] == "running"
                process.kill()
                process.wait(timeout=10)
                stopped = False
                report["forced_exit_code"] = process.returncode
                process, port = launch()
                healthy(process, port)
                recovered = observe(raw_url, run_id)
                assert recovered["run"] == {
                    "status": "aborted",
                    "failure_code": "EVOLUTION_SERVICE_RESTARTED",
                }
                assert recovered["slots"][0] == before["slots"][0]
                assert recovered["slots"][0]["usage_status"] == "known"
                assert recovered["slots"][0]["input_tokens"] == 11
                assert recovered["slots"][0]["output_tokens"] == 13
                assert recovered["slots"][0]["outcome"] == "no_change"
                assert recovered["slots"][0]["llm_cost_usd"] == Decimal("0.0123")
                assert recovered["slots"][1]["outcome"] == "cancelled"
                assert recovered["slots"][1]["usage_status"] == "unknown"
                assert recovered["slots"][1]["error_code"] == "EVOLUTION_SERVICE_RESTARTED"
                process.terminate()
                process.wait(timeout=15)
                report["sigterm_exit_code"] = process.returncode
                process, port = launch()
                healthy(process, port)
                assert observe(raw_url, run_id) == recovered
                assert RejectExternalCalls.calls == 0
                report.update(
                    result="passed",
                    restart_count=2,
                    configured_downstream_requests=0,
                    recovered=recovered,
                    audit_database_retained=True,
                )
        finally:
            if process is not None and process.poll() is None:
                if stopped:
                    process.kill()
                else:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            report["configured_downstream_requests"] = RejectExternalCalls.calls
            sink.shutdown()
            sink.server_close()
            if RejectExternalCalls.calls:
                raise AssertionError("configured downstream requests observed during cleanup")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
