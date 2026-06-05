"""data service 专属 settings。

继承 ``inalpha_shared.Settings``，加 Binance 凭证字段（公开接口可以为空）。
"""
from __future__ import annotations

from functools import lru_cache

from inalpha_shared.config import Settings as BaseSettings
from pydantic import Field


class DataSettings(BaseSettings):
    """data service 的完整 settings。"""

    service_name: str = Field(default="data", alias="SERVICE_NAME")

    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    """OHLCV / ticker 这些公开接口免 key；下私有单 / 查账户才需要。"""

    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")

    # D-9 起：扩到美股 + A股/港股
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    """alpaca-py SDK 的 API key。注册 alpaca.markets 即免费拿；空字符串时 SDK
    会走匿名模式拉公开数据，但限速更严。"""

    alpaca_api_secret: str = Field(default="", alias="ALPACA_API_SECRET")

    fred_api_key: str = Field(default="", alias="FRED_API_KEY")
    """FRED API key（免费）。空字符串时 connector 跳过注册，``venue=fred`` 不可用。
    注册地址 https://fred.stlouisfed.org/docs/api/api_key.html
    """

    data_service_port: int = Field(default=8001, alias="DATA_SERVICE_PORT")

    # --- web_search 并发/超时治理（避免 backend="auto" 长尾把事件循环拖死）---
    web_search_timeout_s: int = Field(default=8, alias="WEB_SEARCH_TIMEOUT_S")
    """ddgs 单引擎 HTTP 超时（秒）。原默认 15s，叠多引擎可到 30s+，收紧到 8s 砍长尾。"""

    web_search_overall_timeout_s: int = Field(
        default=12, alias="WEB_SEARCH_OVERALL_TIMEOUT_S"
    )
    """单次搜索整体超时（秒）。超过即返回 []，避免 backend="auto" 顺序试 8 个引擎把调用方拖死。"""

    web_search_max_concurrency: int = Field(
        default=4, alias="WEB_SEARCH_MAX_CONCURRENCY"
    )
    """同时在飞的搜索数上限。analyst 常 ~10 个并行查询，限并发避免线程池 + GIL 把 async 事件循环饿死。"""


@lru_cache(maxsize=1)
def get_data_settings() -> DataSettings:
    return DataSettings()  # type: ignore[call-arg]
