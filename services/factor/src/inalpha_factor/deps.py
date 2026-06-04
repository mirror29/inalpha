"""FastAPI 依赖：engine 注入。"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from .config import FactorSettings, get_factor_settings
from .engine import FactorEngine


def get_engine(
    settings: Annotated[FactorSettings, Depends(get_factor_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> FactorEngine:
    """每请求构建（无状态、轻量：只持有 adapter 实例）。

    factor 自身不强制 auth（纯只读计算层），但把请求的 Authorization token **透传**给
    下游 data-service —— data ``/bars`` 要求 auth，安全边界落在那一层。无 token 时
    data 会 401，factor 返 502，自然 fail-closed。
    """
    token = ""
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
    return FactorEngine(settings, token=token)


EngineDep = Annotated[FactorEngine, Depends(get_engine)]
SettingsDep = Annotated[FactorSettings, Depends(get_factor_settings)]
