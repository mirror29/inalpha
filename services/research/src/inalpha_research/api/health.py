"""存活探活 —— 不需要 auth。"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .. import __version__
from ..config import ResearchSettings, get_research_settings
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(
    settings: Annotated[ResearchSettings, Depends(get_research_settings)],
) -> HealthResponse:
    """liveness。返回所配 LLM provider 便于调测看 settings 是否生效。"""
    return HealthResponse(
        status="ok",
        service="research",
        version=__version__,
        llm_provider=settings.llm_provider,
    )
