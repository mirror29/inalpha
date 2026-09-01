"""Owner-scoped LLM credential 与 per-run 生命周期测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import httpx
import pytest

from inalpha_evolver.owner_llm import CredentialTemporarilyUnavailable, build_owner_mutator
from inalpha_evolver.runtime.executor import _run_mutator

from .llm_snapshot_fixtures import llm_snapshot


class _Response:
    status_code = 200
    payload: ClassVar[dict[str, str]] = {
        "config_id": "config-1",
        "provider": "deepseek",
        "api_key": "owner-test-key",
    }

    @classmethod
    def json(cls) -> dict[str, str]:
        return cls.payload


class _CredentialClient:
    kwargs: ClassVar[dict[str, object]] = {}
    requested_url: ClassVar[str] = ""
    requested_headers: ClassVar[dict[str, str]] = {}

    def __init__(self, **kwargs: object) -> None:
        type(self).kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> _Response:
        type(self).requested_url = url
        type(self).requested_headers = kwargs["headers"]  # type: ignore[assignment]
        return _Response()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        dashboard_service_url="http://dashboard:3001",
        service_token_ttl_s=3600,
        jwt_secret="test-secret-at-least-32-bytes-long",
        jwt_algorithm="HS256",
        evolver_llm_timeout_s=45,
        evolver_credential_timeout_s=60,
    )


def _run(snapshot: dict | None = None) -> dict:
    return {
        "requested_by_sub": "user:alice",
        "llm_snapshot": snapshot or llm_snapshot(),
        "llm_credential_grant": "signed-credential-grant",
    }


@pytest.mark.asyncio
async def test_owner_mutator_uses_frozen_snapshot_and_credential_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("inalpha_evolver.owner_llm.httpx.AsyncClient", _CredentialClient)
    run = _run()
    settings = _settings()

    mutator = await build_owner_mutator(run, settings)  # type: ignore[arg-type]

    assert _CredentialClient.kwargs["trust_env"] is False
    assert _CredentialClient.kwargs["timeout"] == 60
    assert _CredentialClient.requested_url.endswith("/api/internal/llm-config/config-1")
    assert _CredentialClient.requested_headers["Authorization"] == "Bearer signed-credential-grant"
    assert mutator.llm_client.settings.effective_api_key == "owner-test-key"
    assert mutator.llm_client.settings.llm_model == "deepseek-v4-pro"
    assert mutator.max_output_tokens == 8_192
    assert "api_key" not in run["llm_snapshot"]
    client = await mutator.llm_client._ensure_client()
    assert type(client).__name__ == "AsyncOpenAI"
    await mutator.close()


@pytest.mark.asyncio
async def test_owner_mutator_rejects_missing_snapshot_without_credential_fallback() -> None:
    with pytest.raises(RuntimeError, match="missing frozen LLM snapshot"):
        await build_owner_mutator(
            {"requested_by_sub": "user:alice"},
            _settings(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_owner_mutator_rejects_non_official_frozen_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("inalpha_evolver.owner_llm.httpx.AsyncClient", _CredentialClient)
    snapshot = llm_snapshot()
    snapshot["base_url"] = "http://127.0.0.1:8080/v1"

    with pytest.raises(RuntimeError, match="official provider endpoint"):
        await build_owner_mutator(
            _run(snapshot),
            _settings(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 404])
async def test_owner_mutator_fails_closed_when_credential_service_rejects_request(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    monkeypatch.setattr("inalpha_evolver.owner_llm.httpx.AsyncClient", _CredentialClient)
    monkeypatch.setattr(_Response, "status_code", status_code)

    with pytest.raises(RuntimeError, match=f"HTTP {status_code}"):
        await build_owner_mutator(
            _run(),
            _settings(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_owner_mutator_requeues_when_credential_service_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("inalpha_evolver.owner_llm.httpx.AsyncClient", _CredentialClient)
    monkeypatch.setattr(_Response, "status_code", 503)

    with pytest.raises(CredentialTemporarilyUnavailable, match="HTTP 503"):
        await build_owner_mutator(_run(), _settings())  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, message",
    [
        (
            {"config_id": "config-2", "provider": "deepseek", "api_key": "key"},
            "no longer matches frozen snapshot",
        ),
        (
            {"config_id": "config-1", "provider": "openai", "api_key": "key"},
            "no longer matches frozen snapshot",
        ),
        (
            {"config_id": "config-1", "provider": "deepseek", "api_key": ""},
            "omitted api_key",
        ),
    ],
)
async def test_owner_mutator_rejects_mismatched_or_empty_credentials(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
    message: str,
) -> None:
    monkeypatch.setattr("inalpha_evolver.owner_llm.httpx.AsyncClient", _CredentialClient)
    monkeypatch.setattr(_Response, "status_code", 200)
    monkeypatch.setattr(_Response, "payload", payload)

    with pytest.raises(RuntimeError, match=message):
        await build_owner_mutator(
            _run(),
            _settings(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_owner_mutator_propagates_credential_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_get(*_args: object, **_kwargs: object) -> _Response:
        raise httpx.ReadTimeout("credential lookup timed out")

    monkeypatch.setattr("inalpha_evolver.owner_llm.httpx.AsyncClient", _CredentialClient)
    monkeypatch.setattr(_CredentialClient, "get", fail_get)

    with pytest.raises(CredentialTemporarilyUnavailable, match="ReadTimeout"):
        await build_owner_mutator(_run(), _settings())  # type: ignore[arg-type]


class _ClosableMutator:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_production_mutator_is_closed_but_injected_test_mutator_is_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _ClosableMutator()

    async def build(*_args: object) -> _ClosableMutator:
        return owner

    class ConnectionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> None:
            return None

    cleared: list[object] = []

    async def clear(_conn: object, run_id: object) -> None:
        cleared.append(run_id)

    monkeypatch.setattr("inalpha_evolver.runtime.executor.build_owner_mutator", build)
    monkeypatch.setattr("inalpha_evolver.runtime.executor.get_conn", ConnectionContext)
    monkeypatch.setattr("inalpha_evolver.runtime.executor.runs.clear_credential_grant", clear)
    async with _run_mutator({"run_id": "run-1"}, None, SimpleNamespace()):  # type: ignore[arg-type]
        pass
    assert owner.closed is True
    assert cleared == ["run-1"]

    injected = _ClosableMutator()
    async with _run_mutator({}, injected, SimpleNamespace()):  # type: ignore[arg-type]
        pass
    assert injected.closed is False
