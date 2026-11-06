"""Validate campaign takeover in a fixed local Evolver image, without paid calls.

Requires Docker, local PostgreSQL and installed dev/migration environments.
Creates a fresh migrated audit database and keeps it. Peripheral services are
synthetic HTTP fixtures. Does not authorize production or prove old SDK receipt.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.rows import dict_row


def validate_image(value):
    """Require an immutable own-project business tag, never latest or a foreign registry."""
    if not re.fullmatch(r"ghcr\.io/mirror29/inalpha-evolver:[a-f0-9]{40}", value):
        raise ValueError("expected fixed ghcr.io/mirror29/inalpha-evolver business SHA")
    return value


def container_database_url(url):
    """Map only the validated local database address, preserving optional credentials."""
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("container validation requires a validated local URL")
    credentials = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
    options = [(k, v) for k, v in parse_qsl(parsed.query) if k != "hostaddr"]
    return urlunsplit(
        parsed._replace(
            netloc=credentials + "host.docker.internal:" + str(parsed.port or 5432),
            query=urlencode(options),
        )
    )


def cleanup_owned_process(name, process):
    """Bound each owned-process cleanup step without skipping later resources."""
    record = {"exit_code": None, "errors": []}
    if process is None:
        return record
    for sig in ("CONT", "TERM"):
        try:
            subprocess.run(
                ["docker", "kill", "--signal", sig, name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            record["errors"].append(type(exc).__name__)
    try:
        if process.poll() is None:
            process.wait(timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        record["errors"].append(type(exc).__name__)
    if process.poll() is None:
        try:
            subprocess.run(
                ["docker", "kill", "--signal", "KILL", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            process.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError) as exc:
            record["errors"].append(type(exc).__name__)
    record["exit_code"] = process.poll()
    return record


def terminate_validation(signum, _frame):
    """Unwind through owned-container cleanup on SIGTERM."""
    raise SystemExit(128 + signum)


def main():
    """Run only in an isolated database; retain evidence and remove owned containers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        required=True,
        help="Fixed own Evolver image tagged with its 40-character business SHA",
    )
    args = parser.parse_args()
    image = validate_image(args.image)
    signal.signal(signal.SIGTERM, terminate_validation)
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "services/evolver/scripts"))
    from validate_durable_loop import local_database_urls

    name = "inalpha_container_audit_" + uuid4().hex
    source_url = os.environ.get("DATABASE_URL") or dotenv_values(root / ".env").get("DATABASE_URL")
    if not source_url:
        raise ValueError("local DATABASE_URL required")
    admin, url = local_database_urls(source_url, name)
    raw = url.replace("postgresql+psycopg://", "postgresql://")
    logs = root / ".tmp/e2-takeover-validation" / name
    logs.mkdir(parents=True, mode=0o700)
    probe_dir = root / "services/evolver/scripts/takeover_fixture"
    secret = secrets.token_urlsafe(32)
    fixture = {}
    exchange = logs / "exchange"
    exchange.mkdir(mode=0o777)
    exchange.chmod(0o777)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    endpoint = "http://127.0.0.1:9011"
    env = {k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR") if k in os.environ}
    env.update(
        DATABASE_URL=url,
        JWT_SECRET=secret,
        EVENT_EVOLUTION_ENABLED="true",
        CAMPAIGN_LEASE_TTL_S="30",
        EVOLVER_JOB_TIMEOUT_S="60",
        CANDIDATE_EVALUATION_CONCURRENCY="2",
        EVOLVER_POOL_SIZE="5",
        DATA_SERVICE_URL=endpoint,
        PAPER_SERVICE_URL=endpoint,
        DASHBOARD_SERVICE_URL=endpoint,
        ORCHESTRATION_SERVICE_URL=endpoint,
        FIXTURE_URL=endpoint,
        OMP_NUM_THREADS="1",
        OPENBLAS_NUM_THREADS="1",
        VECLIB_MAXIMUM_THREADS="1",
    )
    python = str(root / "services/evolver/.venv/bin/python")
    child = None
    old_child = None
    old_name = None
    lock = None
    container_name = "inalpha-e2-response-" + uuid4().hex
    container_env = {k: v for k, v in env.items() if k not in ("PATH", "LANG", "TMPDIR")}

    container_env["DATABASE_URL"] = container_database_url(url)
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], text=True, timeout=10
    ).strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("invalid cached Docker image ID")
    for key in (
        "DATA_SERVICE_URL",
        "PAPER_SERVICE_URL",
        "DASHBOARD_SERVICE_URL",
        "ORCHESTRATION_SERVICE_URL",
        "FIXTURE_URL",
    ):
        container_env[key] = endpoint
    env_path = logs / "container.env"
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        for k, v in container_env.items():
            assert "\n" not in v
            f.write(k + "=" + v + "\n")
    report = {
        "database": name,
        "result": "failed",
        "scope": "full actual Evolver application lifespan and dispatchers; provider SDK HTTP transport replaced, synthetic local signer/Dashboard/Data; actual fixed business image, local AMD64 emulation, not real owner/production",
    }
    observer_conn = None
    observer_transient_failures = []

    def observe_once():
        nonlocal observer_conn
        if observer_conn is None or observer_conn.closed:
            observer_conn = psycopg.connect(raw, row_factory=dict_row, autocommit=True)
        with observer_conn.transaction():
            observer_conn.execute("SET TRANSACTION READ ONLY")
            observer_conn.execute("SET LOCAL statement_timeout='5s'")
            row = dict(
                observer_conn.execute(
                    "SELECT status,active_generation,lease_token,lease_expires_at,locked_candidate_id,forward_sandbox_id,holdout_consumed_at,failure_code FROM evolution_campaigns WHERE campaign_id=%s",
                    (fixture["campaign_id"],),
                ).fetchone()
            )
            row["observed_at"] = observer_conn.execute("SELECT clock_timestamp() AS t").fetchone()[
                "t"
            ]
            row["proposal_count"] = (
                observer_conn.execute(
                    "SELECT count(*) AS n FROM evolution_proposal_checkpoints WHERE campaign_id=%s",
                    (fixture["campaign_id"],),
                ).fetchone()["n"]
                if lock is None
                else "locked"
            )
            row["stored_response_count"] = observer_conn.execute(
                "SELECT count(*) AS n FROM evolution_loop_proposal_responses WHERE loop_id=%s AND response IS NOT NULL",
                (fixture["loop_id"],),
            ).fetchone()["n"]
            row["response_count"] = observer_conn.execute(
                "SELECT count(*) AS n FROM evolution_loop_proposal_responses WHERE loop_id=%s",
                (fixture["loop_id"],),
            ).fetchone()["n"]
            row["succeeded"] = observer_conn.execute(
                "SELECT count(*) AS n FROM evolution_implementations WHERE campaign_id=%s AND outcome='succeeded'",
                (fixture["campaign_id"],),
            ).fetchone()["n"]
            row["ledger"] = dict(
                observer_conn.execute(
                    "SELECT spent_usd,reserved_usd,max_cost_usd,(SELECT count(*) FROM evolution_loop_cost_reservations r WHERE r.loop_id=a.loop_id) AS attempts FROM evolution_loop_authorizations a WHERE loop_id=%s",
                    (fixture["loop_id"],),
                ).fetchone()
            )
            row["expired"] = observer_conn.execute(
                "SELECT lease_expires_at<clock_timestamp() AS expired FROM evolution_campaigns WHERE campaign_id=%s",
                (fixture["campaign_id"],),
            ).fetchone()["expired"]
            return row

    def observe():
        nonlocal observer_conn
        for attempt in range(3):
            try:
                return observe_once()
            except psycopg.OperationalError as exc:
                if exc.sqlstate is not None:
                    raise
                observer_transient_failures.append(type(exc).__name__)
                if observer_conn is not None:
                    observer_conn.close()
                observer_conn = None
                if attempt == 2:
                    raise
                time.sleep(0.5)

    def start(out, stage):
        return subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--platform",
                "linux/amd64",
                "--cpus",
                "2",
                "--memory",
                "4g",
                "--name",
                container_name,
                "--env-file",
                str(env_path),
                "--add-host",
                "host.docker.internal:host-gateway",
                "-e",
                "INSTANCE_STAGE=" + stage,
                "-v",
                str(exchange) + ":/probe/exchange:rw",
                "-p",
                f"127.0.0.1:{port}:8005",
                "-v",
                str(probe_dir / "app.py") + ":/probe/app.py:ro",
                "-v",
                str(probe_dir / "stale_write.py") + ":/probe/stale_write.py:ro",
                "--entrypoint",
                ".venv/bin/python",
                image_id,
                "-m",
                "uvicorn",
                "app:app",
                "--app-dir",
                "/probe",
                "--host",
                "0.0.0.0",
                "--port",
                "8005",
                "--workers",
                "1",
            ],
            cwd=logs,
            env=os.environ,
            stdout=out,
            stderr=out,
            start_new_session=True,
        )

    def kill_container(sig):
        subprocess.run(
            ["docker", "kill", "--signal", sig, container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )

    def health():
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            assert child.poll() is None
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as r:
                    p = json.load(r)
                    if p["status"] == "ok" and p["event_evolution"] == "enabled":
                        return p
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise TimeoutError("health")

    stage = "create_audit_database"
    try:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        stage = "migrate_audit_database"
        with (logs / "migration.log").open("w") as out:
            subprocess.run(
                [
                    str(root / "infra/migrations/.venv/bin/python"),
                    "-m",
                    "alembic",
                    "upgrade",
                    "head",
                ],
                cwd=root / "infra/migrations",
                env=env,
                stdout=out,
                stderr=out,
                timeout=120,
                check=True,
            )
        stage = "seed_synthetic_baseline"
        with (logs / "seed.log").open("w") as out:
            subprocess.run(
                [python, str(probe_dir / "seed.py"), str(logs / "fixture.json")],
                cwd=root,
                env=env,
                stdout=out,
                stderr=out,
                timeout=30,
                check=True,
            )
        fixture.update(json.loads((logs / "fixture.json").read_text()))
        (exchange / "fixture.json").write_text(json.dumps(fixture))
        with (logs / "app.log").open("w") as out:
            stage = "initial_app_health"
            child = start(out, "before")
            report["initial_health"] = health()
            stage = "first_offline_calls"
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if (exchange / "calls").exists() and len(
                    (exchange / "calls").read_text().splitlines()
                ) >= 2:
                    break
                assert child.poll() is None
                time.sleep(0.1)
            assert (exchange / "calls").exists() and len(
                (exchange / "calls").read_text().splitlines()
            ) == 2
            before = observe()
            assert before["response_count"] == 2 and before["stored_response_count"] == 0
            assert before["ledger"]["attempts"] == 2 and before["ledger"]["spent_usd"] == 0
            assert (
                before["ledger"]["reserved_usd"] > 0
                and before["ledger"]["max_cost_usd"] > before["ledger"]["reserved_usd"] * 2
            )
            stage = "live_write_positive_control"
            with psycopg.connect(raw, row_factory=dict_row) as control_conn:
                control_conn.execute("SET TRANSACTION READ ONLY")
                control_conn.execute("SET LOCAL statement_timeout='5s'")
                reservation = control_conn.execute(
                    "SELECT reservation_id FROM evolution_loop_proposal_responses WHERE loop_id=%s AND response IS NULL ORDER BY batch LIMIT 1",
                    (fixture["loop_id"],),
                ).fetchone()
            (exchange / "stale_scope.json").write_text(
                json.dumps(
                    {
                        **fixture,
                        "old_lease_token": str(before["lease_token"]),
                        "new_lease_token": str(before["lease_token"]),
                        "reservation_id": str(reservation["reservation_id"]),
                    }
                )
            )
            control_run = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_name,
                    ".venv/bin/python",
                    "/probe/stale_write.py",
                    "--positive-control",
                ],
                capture_output=True,
                text=True,
                timeout=20,
            )
            (logs / "positive-control.log").write_text(control_run.stdout + control_run.stderr)
            if control_run.returncode != 0:
                raise RuntimeError("live checkpoint positive control failed")
            report["live_write_positive_control"] = json.loads(control_run.stdout.strip())
            assert report["live_write_positive_control"]["result"] == "accepted_rolled_back"
            after_control = observe()
            assert (
                after_control["ledger"] == before["ledger"]
                and after_control["stored_response_count"] == 0
            )
            stage = "pause_old_application"
            kill_container("STOP")
            report["sigstop_completed_at"] = datetime.now(UTC).isoformat()
            old_child = child
            old_name = container_name
            report["fault"] = (
                "SIGSTOP old application; wait natural lease expiry; second application claims"
            )
            before = observe()
            assert before["proposal_count"] == 0
            report["before"] = before
            stage = "observe_natural_expiry"
            deadline = time.monotonic() + 45
            while not observe()["expired"]:
                if time.monotonic() > deadline:
                    raise TimeoutError("lease did not expire")
                time.sleep(0.5)
            expired = observe()
            assert (
                expired["expired"] is True and expired["observed_at"] > expired["lease_expires_at"]
            )
            report["expired_lease_observation"] = expired
            stage = "acquire_observation_lock"
            lock = psycopg.connect(raw)
            lock.execute("SET LOCAL statement_timeout='5s'")
            lock.execute("LOCK TABLE evolution_loop_proposal_responses IN ACCESS EXCLUSIVE MODE")
            report["observation_lock_acquired_at"] = datetime.now(UTC).isoformat()
            container_name = "inalpha-e2-response-" + uuid4().hex
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            report["second_application_started_at"] = datetime.now(UTC).isoformat()
            stage = "takeover_app_health"
            child = start(out, "after")
            report["restart_health"] = health()
            stage = "observe_replacement_claim"
            deadline = time.monotonic() + 30
            with psycopg.connect(raw, row_factory=dict_row) as claim_conn:
                while time.monotonic() < deadline:
                    with claim_conn.transaction():
                        claim_conn.execute("SET TRANSACTION READ ONLY")
                        claim_conn.execute("SET LOCAL statement_timeout='5s'")
                        claim = dict(
                            claim_conn.execute(
                                "SELECT status,lease_token,lease_expires_at,updated_at,clock_timestamp() AS observed_at FROM evolution_campaigns WHERE campaign_id=%s",
                                (fixture["campaign_id"],),
                            ).fetchone()
                        )
                    if (
                        claim["lease_token"] is not None
                        and claim["lease_token"] != before["lease_token"]
                    ):
                        break
                    time.sleep(0.1)
            assert (
                claim["lease_token"] is not None
                and claim["lease_token"] != before["lease_token"]
                and claim["status"] == "replaying"
            )
            assert (
                claim["updated_at"] > expired["lease_expires_at"]
                and claim["observed_at"] > expired["observed_at"]
            )
            report["second_application_claim"] = claim
            (exchange / "stale_scope.json").write_text(
                json.dumps(
                    {
                        **fixture,
                        "old_lease_token": str(before["lease_token"]),
                        "reservation_id": str(reservation["reservation_id"]),
                        "new_lease_token": str(claim["lease_token"]),
                    }
                )
            )
            stage = "stale_write_rejection"
            stale_run = subprocess.run(
                ["docker", "exec", container_name, ".venv/bin/python", "/probe/stale_write.py"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            (logs / "stale-write.log").write_text(stale_run.stdout + stale_run.stderr)
            if stale_run.returncode != 0:
                raise RuntimeError("isolated stale-write probe failed; see private subprocess log")
            report["direct_stale_write"] = json.loads(stale_run.stdout.strip())
            assert report["direct_stale_write"]["result"] == "rejected"
            lock.rollback()
            lock.close()
            lock = None
            report["observation_lock_released_at"] = datetime.now(UTC).isoformat()
            stage = "unknown_response_stop"
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                after = observe()
                if after["status"] in (
                    "insufficient_evidence",
                    "failed",
                    "candidate_locked",
                    "waiting_forward",
                ):
                    break
                assert child.poll() is None
                time.sleep(0.5)
            calls = (exchange / "calls").read_text().splitlines()
            requests = (exchange / "requests").read_text().splitlines()
            report["after"] = after
            report["offline_provider_calls"] = calls
            report["local_peripheral_requests"] = requests
            assert (
                after["status"] == "failed"
                and after["failure_code"] == "LOOP_PROPOSAL_RESPONSE_UNCONFIRMED"
            )
            assert after["active_generation"] == 1 and after["succeeded"] == 0
            assert after["proposal_count"] == 0 and after["stored_response_count"] == 0
            assert after["response_count"] == 2 and after["lease_token"] != before["lease_token"]
            assert len(calls) == 2 and calls.count("before") == 2
            assert after["ledger"] == before["ledger"]
            subprocess.run(
                ["docker", "kill", "--signal", "CONT", old_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=10,
            )
            (exchange / "release").touch()
            report["old_application_resumed_and_fixture_released_at"] = datetime.now(
                UTC
            ).isoformat()
            stage = "resume_old_application_invariance"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                final = observe()
                assert (
                    final["status"] == after["status"]
                    and final["failure_code"] == after["failure_code"]
                )
                assert (
                    final["ledger"] == after["ledger"]
                    and final["response_count"] == 2
                    and final["stored_response_count"] == 0
                )
                assert (
                    final["proposal_count"] == 0
                    and final["succeeded"] == 0
                    and final["lease_token"] == after["lease_token"]
                )
                assert (exchange / "calls").read_text().splitlines() == calls
                assert old_child.poll() is None and child.poll() is None
                time.sleep(0.5)
            report["after_old_worker_return"] = final
            report["late_response_observation_seconds"] = 20
            report["purpose"] = (
                "Observe takeover/resume invariance and direct old-token checkpoint write rejection; not successful old SDK receipt or full fencing acceptance"
            )
            report["result"] = "passed"
    except Exception as exc:
        report["error_type"] = type(exc).__name__
        report["failure_stage"] = stage
        try:
            failure_path = logs / "failure-traceback.log"
            fd = os.open(failure_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as output:
                traceback.print_exc(file=output)
            report["private_failure_traceback"] = "failure-traceback.log"
        except OSError:
            report["private_failure_traceback"] = "not_written"

    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        report["cleanup_errors"] = []
        for connection in (lock, observer_conn):
            if connection is not None:
                try:
                    connection.close()
                except psycopg.Error as exc:
                    report["cleanup_errors"].append(type(exc).__name__)
        report["observer_transient_failures"] = observer_transient_failures
        report["old_worker_cleanup"] = cleanup_owned_process(old_name, old_child)
        if child is not old_child:
            report["new_worker_cleanup"] = cleanup_owned_process(container_name, child)
        try:
            env_path.unlink(missing_ok=True)
        except OSError as exc:
            report["cleanup_errors"].append(type(exc).__name__)
        report["env_file_removed"] = not env_path.exists()
        try:
            check = subprocess.run(
                ["docker", "ps", "-a", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            report["owned_container_removed"] = check.returncode == 0 and not (
                {container_name, old_name} & set(check.stdout.splitlines())
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            report["owned_container_removed"] = False
            report["cleanup_errors"].append(type(exc).__name__)
        if not report["owned_container_removed"] or not report["env_file_removed"]:
            report["result"] = "failed"
            report["cleanup_error"] = "owned-resource cleanup not confirmed"
        report["image"] = image
        report["actual_image_id"] = image_id
        report["runtime"] = (
            "local Docker AMD64 emulation on ARM host; CPUs2/memory4GB; no production container restarted"
        )
        report["entrypoint_shim_sha256"] = hashlib.sha256(
            (probe_dir / "app.py").read_bytes()
        ).hexdigest()
        (logs / "receipt.json").write_text(json.dumps(report, default=str, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "result": report["result"],
                    "error_type": report.get("error_type"),
                    "database": name,
                    "receipt": str(logs / "receipt.json"),
                }
            )
        )
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
