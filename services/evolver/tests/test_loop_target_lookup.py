"""An owner can resume the same workflow from its target or its E1 detail page."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.db import _db_dep

from inalpha_evolver.api.loop_routes import router
from inalpha_evolver.storage import loops, runs

from .test_loop_storage import create_baseline, loop_database  # noqa: F401


@pytest.mark.asyncio
async def test_target_lookup_resumes_owned_loop_and_never_restarts_its_baseline(database):
    owner = uuid4()
    args = await create_baseline(database, owner)
    loop = await loops.ensure_for_e1_run(database, **args)
    app = FastAPI()
    app.include_router(router)

    async def connection():
        yield database

    app.dependency_overrides[_db_dep] = connection
    app.dependency_overrides[get_current_user] = lambda: User(user_id=str(owner))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://evolver.test"
    ) as client:

        async def lookup(kind, target):
            response = await client.get(
                "/evolution-loops/for-target",
                params={"target_kind": kind, "target_id": str(target)},
            )
            assert response.status_code == 200, response.text
            return response.json()

        assert (await lookup(args["target_kind"], args["target_id"]))["loop_id"] == str(
            loop["loop_id"]
        )
        assert (await lookup("e1_run", args["e1_run_id"]))["loop_id"] == str(loop["loop_id"])
        app.dependency_overrides[get_current_user] = lambda: User(user_id=str(uuid4()))
        assert await lookup("e1_run", args["e1_run_id"]) is None
        assert await lookup(args["target_kind"], args["target_id"]) is None
        app.dependency_overrides[get_current_user] = lambda: User(user_id=str(owner))
        await runs.transition(
            database, args["e1_run_id"], from_statuses=("queued",), to_status="failed"
        )
        assert (await lookup("e1_run", args["e1_run_id"]))["status"] == "failed"
        assert await lookup(args["target_kind"], args["target_id"]) is None
