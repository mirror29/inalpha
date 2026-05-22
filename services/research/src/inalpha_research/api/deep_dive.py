"""``POST /deep_dive`` —— 单次跑 TradingAgents 风格研究链路。

D-8b 范围：同步调用，单 deep dive < 90s（DeepSeek API + 2 analyst + 1 manager）。
D-9+ 升级 async + jobId + polling/WS（ADR-0002 §长任务）。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import UnauthorizedError

from ..config import ResearchSettings, get_research_settings
from ..data_client import DataClient
from ..llm.client import build_llm_client
from ..runner import run_deep_dive
from ..schemas import DeepDiveRequest, ResearchPlan

router = APIRouter(tags=["research"])


@router.post("/deep_dive", response_model=ResearchPlan)
async def post_deep_dive(
    req: DeepDiveRequest,
    settings: Annotated[ResearchSettings, Depends(get_research_settings)],
    _user: Annotated[User, Depends(get_current_user)],
    authorization: Annotated[str | None, Header()] = None,
) -> ResearchPlan:
    """跑研究：每次都新建 LLM + DataClient（D-8b 单次，连接池等 D-9 再加）。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedError("missing Authorization header")
    user_token = authorization.removeprefix("Bearer ").strip()

    llm = build_llm_client(
        provider=settings.llm_provider,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )

    try:
        async with DataClient(settings.data_service_url, user_token) as data:
            return await run_deep_dive(req, llm=llm, data=data)
    finally:
        await llm.aclose()
