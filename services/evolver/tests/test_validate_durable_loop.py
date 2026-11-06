"""Local-only connection target validation before any database or subprocess access."""

import importlib.util
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import create_engine

_PATH = Path(__file__).resolve().parents[1] / "scripts/validate_durable_loop.py"
_SPEC = importlib.util.spec_from_file_location("validate_durable_loop", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
validation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(validation)


@pytest.mark.parametrize(
    "query",
    [
        "host=remote.example",
        "hostaddr=203.0.113.1",
        "dbname=existing_db",
        "service=production",
        "servicefile=/tmp/service.conf",
        "port=6543",
        "options=-csearch_path=other",
        "sslmode=disable&sslmode=require",
        "%68ost=remote.example",
    ],
)
def test_routing_options_are_rejected_before_connecting(query):
    with pytest.raises(ValueError, match="without target overrides"):
        validation.local_database_urls(
            f"postgresql://localhost/dev?{query}", "inalpha_loop_check_test"
        )


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_effective_targets_ignore_ambient_hostaddr_and_database(host, monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "203.0.113.1")
    monkeypatch.setenv("PGDATABASE", "existing_db")
    monkeypatch.setenv("PGPORT", "6543")
    admin, test = validation.local_database_urls(
        f"postgresql+psycopg://quant@{host}:5433/dev?sslmode=disable",
        "inalpha_loop_check_test",
    )
    address = "::1" if host == "[::1]" else "127.0.0.1"
    effective = conninfo_to_dict(admin)
    assert effective["hostaddr"] == address and effective["dbname"] == "dev"
    engine = create_engine(test)
    _, params = engine.dialect.create_connect_args(engine.url)
    assert params["hostaddr"] == address
    assert params["dbname"] == "inalpha_loop_check_test"
    assert params["port"] == 5433
    assert urlsplit(test).scheme == "postgresql+psycopg"


def test_default_port_is_explicit_and_credentials_are_preserved():
    fixture_password = "fixture@password"
    source = urlunsplit(
        ("postgresql", f"quant:{quote(fixture_password, safe='')}@localhost", "/dev", "", "")
    )
    admin, _ = validation.local_database_urls(source, "inalpha_loop_check_test")
    effective = conninfo_to_dict(admin)
    assert effective["password"] == fixture_password
    assert effective["port"] == "5432"


def test_discovery_includes_new_files_and_only_excludes_opt_in_suite(tmp_path):
    for name in [
        "test_loop_storage.py",
        "test_loop_new_recovery.py",
        "test_loop_five_generations.py",
        "test_other.py",
    ]:
        (tmp_path / name).touch()
    assert validation.discover_tests(tmp_path, include_five_generations=False) == [
        "test_loop_new_recovery.py",
        "test_loop_storage.py",
    ]
    assert len(validation.discover_tests(tmp_path, include_five_generations=True)) == 3


def test_empty_discovery_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="No durable-loop"):
        validation.discover_tests(tmp_path, include_five_generations=False)


def test_missing_opt_in_suite_fails_loudly(tmp_path):
    (tmp_path / "test_loop_storage.py").touch()
    with pytest.raises(ValueError, match="opt-in contract"):
        validation.discover_tests(tmp_path, include_five_generations=True)


def test_repository_opt_in_adds_the_real_five_generation_suite():
    test_dir = Path(__file__).parent
    default = set(validation.discover_tests(test_dir, include_five_generations=False))
    complete = set(validation.discover_tests(test_dir, include_five_generations=True))
    assert complete - default == {"test_loop_five_generations.py"}


def test_migration_timeout_still_drops_created_database(tmp_path, monkeypatch):
    test_dir = tmp_path / "services/evolver/tests"
    test_dir.mkdir(parents=True)
    for name in ["test_loop_storage.py", "test_loop_five_generations.py"]:
        (test_dir / name).touch()
    statements = []

    class Admin:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            statements.append(str(statement))

    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 120
        raise validation.subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(validation, "ROOT", tmp_path)
    monkeypatch.setattr(validation.psycopg, "connect", lambda *args, **kwargs: Admin())
    monkeypatch.setattr(validation.subprocess, "run", timeout)
    monkeypatch.setattr(validation.signal, "signal", lambda *args: None)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/dev")
    monkeypatch.setattr("sys.argv", ["validate_durable_loop.py"])
    assert validation.main() == 124
    assert any("CREATE DATABASE" in statement for statement in statements)
    assert any("DROP DATABASE" in statement for statement in statements)


def test_sigterm_unwinds_for_cleanup():
    with pytest.raises(SystemExit) as stopped:
        validation.terminate_validation(validation.signal.SIGTERM, None)
    assert stopped.value.code == 143
