"""GET /catalog —— 列出所有因子定义。"""
from __future__ import annotations

from fastapi import APIRouter

from ..deps import EngineDep
from ..schemas import CatalogResponse, FactorSpecOut

router = APIRouter(tags=["factor"])


@router.get("/catalog", response_model=CatalogResponse)
async def catalog(engine: EngineDep) -> CatalogResponse:
    """因子目录。``available=false`` 的源（如未启用 qlib）仍露出，便于前端/agent 知道存在。"""
    sources = engine.sources()
    out: list[FactorSpecOut] = []
    for s in engine.catalog():
        src_avail = sources.get(s.source, False)
        out.append(
            FactorSpecOut(
                factor_id=s.factor_id,
                source=s.source,
                name=s.name,
                kind=s.kind,
                needs_universe=s.needs_universe,
                direction_hint=s.direction_hint,
                available=src_avail,
                extras=s.extras,
            )
        )
    return CatalogResponse(factors=out, sources=sources)
