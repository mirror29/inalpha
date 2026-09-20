"""The research execution policy is read-only and restricted to the Evolver service."""

import httpx
import pytest
from fastapi import FastAPI
from inalpha_shared.config import Settings, get_settings
from inalpha_shared.middleware import install_error_handler

from inalpha_paper.api.evolution_forward import router
from inalpha_paper.config import PaperSettings
from inalpha_paper.evolution_execution_policy import (
    EvolutionExecutionPolicy,
    frozen_protection_kwargs,
)

from .test_evolution_forward_auth import _SECRET, _token


def test_frozen_policy_does_not_follow_settings_changes():
    settings = PaperSettings(INALPHA_PROTECTIVE_STOP_LOSS_PCT=0.2)
    frozen = EvolutionExecutionPolicy.from_settings(settings).model_dump(mode="json")
    settings.protective_stop_loss_pct = 0.05
    assert frozen_protection_kwargs({"protection_policy": frozen})["protective_stop_loss_pct"] == 0.2
    assert frozen_protection_kwargs({}) == {}
    with pytest.raises(ValueError):
        frozen_protection_kwargs({"protection_policy": {"version": "unknown"}})


@pytest.mark.asyncio
async def test_policy_read_requires_its_own_service_purpose():
    app = FastAPI()
    install_error_handler(app)
    app.include_router(router)
    app.dependency_overrides[get_settings] = lambda: Settings(JWT_SECRET=_SECRET)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://paper") as client:
        for token in [_token(), _token(token_use="owner")]:
            response = await client.get("/internal/evolution-forward/execution-policy", headers={"Authorization": f"Bearer {token}"})
            assert response.status_code == 401
        response = await client.get("/internal/evolution-forward/execution-policy", headers={
            "Authorization": f"Bearer {_token(token_purpose='evolution_execution_policy_read')}",
        })
        assert response.status_code == 200
        assert response.json()["version"] == "paper-protection-v1"
        assert "protective_stop_loss_pct" in response.json()
