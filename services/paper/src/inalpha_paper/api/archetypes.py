"""``GET /archetypes`` —— 策略原型库目录（ADR-0051 D1）。

只读、静态、纯函数：把 ``strategy_authoring.archetypes`` 的原型目录透给 orchestration 层，
agent 写策略前按因子 kind 取匹配骨架当起点。无 DB、无副作用。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from inalpha_shared.auth import User, get_current_user
from pydantic import BaseModel, Field

from ..strategy_authoring.archetypes import ArchetypeMeta, list_archetypes

router = APIRouter(tags=["archetypes"])


class ArchetypeParamOut(BaseModel):
    name: str
    default: float
    doc: str


class ArchetypeOut(BaseModel):
    name: str
    source_archetype: str = Field(
        description="源 canonical（MIT 出处）；Inalpha 特有的为空串",
    )
    applies_to_kinds: list[str]
    description: str
    when_to_use: str
    when_not_to_use: str
    failure_modes: list[str]
    compatible_pivots: list[str]
    params: list[ArchetypeParamOut]
    code: str = Field(description="完整可跑候选源码（过沙盒三审）；agent 以此为起点改参再 author")


class ArchetypesResponse(BaseModel):
    archetypes: list[ArchetypeOut]


def _to_out(meta: ArchetypeMeta) -> ArchetypeOut:
    return ArchetypeOut(
        name=meta.name,
        source_archetype=meta.source_archetype,
        applies_to_kinds=list(meta.applies_to_kinds),
        description=meta.description,
        when_to_use=meta.when_to_use,
        when_not_to_use=meta.when_not_to_use,
        failure_modes=list(meta.failure_modes),
        compatible_pivots=list(meta.compatible_pivots),
        params=[
            ArchetypeParamOut(name=p.name, default=float(p.default), doc=p.doc)
            for p in meta.params
        ],
        code=meta.code,
    )


@router.get("/archetypes", response_model=ArchetypesResponse)
async def get_archetypes(
    _user: Annotated[User, Depends(get_current_user)],
    factor_kinds: Annotated[
        str | None,
        Query(
            description="逗号分隔的因子 kind（如 'momentum,trend'）；匹配的骨架排前面，不过滤",
        ),
    ] = None,
) -> ArchetypesResponse:
    """列出策略原型；给 ``factor_kinds`` 时把匹配 kind 的骨架排前面。"""
    kinds = (
        [k for k in (factor_kinds.split(",")) if k.strip()] if factor_kinds else None
    )
    metas = list_archetypes(kinds)
    return ArchetypesResponse(archetypes=[_to_out(m) for m in metas])
