"""Owner-scoped cost evidence for the durable loop detail page."""

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import _db_dep
from inalpha_shared.middleware import install_error_handler

from inalpha_evolver.api.loop_routes import router
from inalpha_evolver.storage import loop_authorizations, loops

from .test_loop_storage import create_baseline, loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_budget_projection_distinguishes_missing_authority_and_owner(database):
    owner = uuid4()
    args = await create_baseline(database, owner)
    loop = await loops.ensure_for_e1_run(database, **args)
    assert await loop_authorizations.budget_summary(database, loop["loop_id"], owner) is None
    await loop_authorizations.register(
        database, loop_id=loop["loop_id"], owner_account_id=owner,
        request_digest="a" * 64, max_cost_usd=Decimal("0.5"),
    )
    await database.execute(
        "UPDATE evolution_loop_authorizations SET spent_usd=0.12,reserved_usd=0.03 WHERE loop_id=%s",
        (loop["loop_id"],),
    )
    assert await loop_authorizations.budget_summary(database, loop["loop_id"], owner) == {
        "max_cost_usd": Decimal("0.5"), "spent_usd": Decimal("0.12"),
        "reserved_usd": Decimal("0.03"), "available_usd": Decimal("0.35"),
    }
    assert await loop_authorizations.budget_summary(database, loop["loop_id"], uuid4()) is None


@pytest.mark.asyncio
async def test_detail_api_exposes_costs_but_not_authorization_secrets(database):
    owner = uuid4()
    args = await create_baseline(database, owner)
    loop = await loops.ensure_for_e1_run(database, **args)
    await loop_authorizations.register(
        database, loop_id=loop["loop_id"], owner_account_id=owner,
        request_digest="b" * 64, max_cost_usd=Decimal("0.5"),
    )
    app = FastAPI()
    install_error_handler(app)
    app.include_router(router)

    async def connection():
        yield database

    app.dependency_overrides[_db_dep] = connection
    app.dependency_overrides[get_current_user] = lambda: User(user_id=str(owner))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evolver.test",
    ) as client:
        response = await client.get(f"/evolution-loops/{loop['loop_id']}")
        assert response.status_code == 200
        assert response.json()["budget_usage"] == {
            "max_cost_usd": 0.5, "spent_usd": 0, "reserved_usd": 0, "available_usd": 0.5,
        }
        assert "request_digest" not in response.text
        assert "credential_grant" not in response.text
        app.dependency_overrides[get_current_user] = lambda: User(user_id=str(uuid4()))
        denied = await client.get(f"/evolution-loops/{loop['loop_id']}")
        assert denied.status_code == 404
        assert "budget_usage" not in denied.text
