"""data service 专属 settings。

继承 ``inalpha_shared.Settings``，加 Binance 凭证字段（公开接口可以为空）。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from inalpha_shared.config import Settings as BaseSettings


class DataSettings(BaseSettings):
    """data service 的完整 settings。"""

    service_name: str = Field(default="data", alias="SERVICE_NAME")

    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    """OHLCV / ticker 这些公开接口免 key；下私有单 / 查账户才需要。"""

    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")

    data_service_port: int = Field(default=8001, alias="DATA_SERVICE_PORT")


@lru_cache(maxsize=1)
def get_data_settings() -> DataSettings:
    return DataSettings()  # type: ignore[call-arg]
