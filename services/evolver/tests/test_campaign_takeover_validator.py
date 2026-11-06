"""Protect the offline container validator's image and local-connection boundaries."""

import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).parents[1] / "scripts" / "validate_campaign_takeover.py"
spec = importlib.util.spec_from_file_location("takeover_validator", path)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/mirror29/inalpha-evolver:latest",
        "foreign/image:" + "a" * 40,
        "ghcr.io/mirror29/inalpha-evolver:" + "a" * 39,
    ],
)
def test_requires_fixed_own_business_image(image):
    """Reject moving aliases and foreign images before starting containers."""
    with pytest.raises(ValueError):
        validator.validate_image(image)


def test_container_connection_mapping_preserves_credentials_without_hostaddr_override():
    """Only remap the host; do not rewrite address-like values inside credentials."""
    result = validator.container_database_url(
        "postgresql+psycopg://fixture:offline-127.0.0.1@localhost:5432/a?hostaddr=127.0.0.1&connect_timeout=5"
    )
    assert (
        result
        == "postgresql+psycopg://fixture:offline-127.0.0.1@host.docker.internal:5432/a?connect_timeout=5"
    )
    assert (
        validator.container_database_url("postgresql+psycopg://localhost:5432/a?hostaddr=127.0.0.1")
        == "postgresql+psycopg://host.docker.internal:5432/a"
    )


def test_remote_database_address_is_rejected():
    """A remote database must not be adapted into an apparently local target."""
    with pytest.raises(ValueError):
        validator.container_database_url("postgresql+psycopg://remote.example:5432/a")


def test_cleanup_continues_after_signal_timeout(monkeypatch):
    """A timed-out signal must not prevent the remaining cleanup sequence."""
    import subprocess
    from unittest.mock import Mock

    process = Mock()
    process.poll.side_effect = [None, None, 0]
    process.wait.side_effect = [subprocess.TimeoutExpired("wait", 30), 0]
    runner = Mock(
        side_effect=[
            subprocess.TimeoutExpired("docker", 10),
            Mock(returncode=0),
            Mock(returncode=0),
        ]
    )
    monkeypatch.setattr(validator.subprocess, "run", runner)
    result = validator.cleanup_owned_process("inalpha-e2-response-fixture", process)
    assert result["exit_code"] == 0
    assert result["errors"] == ["TimeoutExpired", "TimeoutExpired"]
    assert runner.call_args_list[-1].args[0][-2:] == ["KILL", "inalpha-e2-response-fixture"]


def test_cleanup_tolerates_already_exited_container(monkeypatch):
    """Missing-container signal results do not abort receipt and env cleanup."""
    from unittest.mock import Mock

    process = Mock()
    process.poll.return_value = 0
    monkeypatch.setattr(validator.subprocess, "run", Mock(return_value=Mock(returncode=1)))
    assert validator.cleanup_owned_process("inalpha-e2-response-fixture", process) == {
        "exit_code": 0,
        "errors": [],
    }


def test_portless_local_database_uses_postgres_default():
    assert (
        validator.container_database_url("postgresql+psycopg://fixture@localhost/a")
        == "postgresql+psycopg://fixture@host.docker.internal:5432/a"
    )


def test_reused_baseline_fixture_contract_remains_available():
    """Keep the intentionally reused dev fixture discoverable when tests are refactored."""
    import inspect

    from .test_loop_handoff import ready_loop

    assert inspect.iscoroutinefunction(ready_loop)
