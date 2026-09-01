"""Data event snapshot client error-contract tests."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from inalpha_shared.errors import InalphaError, NotFoundError

from inalpha_evolver import event_client
from inalpha_evolver.config import EvolverSettings

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: httpx.MockTransport,
) -> None:
    monkeypatch.setattr(
        event_client.httpx,
        "AsyncClient",
        lambda **kwargs: _REAL_ASYNC_CLIENT(transport=handler, timeout=kwargs["timeout"]),
    )


@pytest.mark.asyncio
async def test_snapshot_client_preserves_data_not_found_contract(monkeypatch) -> None:
    snapshot_id = uuid4()
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            404,
            json={
                "detail": {
                    "code": "EVENT_RECORD_NOT_FOUND",
                    "message": "snapshot does not exist",
                    "details": {},
                }
            },
        )
    )
    _patch_transport(monkeypatch, transport)

    with pytest.raises(NotFoundError) as error:
        await event_client.fetch_event_snapshot(
            snapshot_id,
            owner_account_id=uuid4(),
            settings=EvolverSettings(),
        )

    assert error.value.status_code == 404
    assert error.value.code == "EVENT_RECORD_NOT_FOUND"


@pytest.mark.asyncio
async def test_snapshot_client_maps_bad_identity_to_upstream_failure(monkeypatch) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"snapshot_id": str(uuid4())})
    )
    _patch_transport(monkeypatch, transport)

    with pytest.raises(InalphaError) as error:
        await event_client.fetch_event_snapshot(
            uuid4(),
            owner_account_id=uuid4(),
            settings=EvolverSettings(),
        )

    assert error.value.status_code == 502
    assert error.value.code == "EVENT_SNAPSHOT_IDENTITY_MISMATCH"
