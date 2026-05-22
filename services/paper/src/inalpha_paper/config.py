"""paper service 专属 settings。

继承 ``inalpha_shared.Settings``，加 ``DATA_SERVICE_URL`` 字段（跨服务调用 data 用）。
"""
from __future__ import annotations

from functools import lru_cache

from inalpha_shared.config import Settings as BaseSettings
from pydantic import Field


class PaperSettings(BaseSettings):
    """paper service 完整 settings。"""

    service_name: str = Field(default="paper", alias="SERVICE_NAME")

    data_service_url: str = Field(
        default="http://localhost:8001",
        alias="DATA_SERVICE_URL",
        description="data-service 的 base URL，paper 拉 K 线时走这里。",
    )

    paper_service_port: int = Field(default=8002, alias="PAPER_SERVICE_PORT")


@lru_cache(maxsize=1)
def get_paper_settings() -> PaperSettings:
    return PaperSettings()  # type: ignore[call-arg]
