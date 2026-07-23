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

    web_search_overall_timeout_s: int = Field(default=20, alias="WEB_SEARCH_OVERALL_TIMEOUT_S")
    """单次搜索整体超时（秒），避免 backend="auto" 顺序试 8 个引擎把调用方拖死。
    12s 对 bing 中文查询偏紧（本地网络实测常恰好掐死）；auto 失败会换引擎再兜一次，
    最坏耗时 = 2 × 本值。"""

    web_search_max_concurrency: int = Field(default=4, alias="WEB_SEARCH_MAX_CONCURRENCY")
    """同时在飞的搜索数上限。analyst 常 ~10 个并行查询，限并发避免线程池 + GIL 把 async 事件循环饿死。"""

    web_search_cache_ttl_s: int = Field(default=600, alias="WEB_SEARCH_CACHE_TTL_S")
    """搜索结果进程内缓存 TTL（秒）。深扫一轮常复用同 query；0 = 关缓存。只缓存非空结果。"""

    # --- web_fetch 网页正文抓取（证据链：把 URL 变成可引用的正文） ---
    web_fetch_timeout_s: int = Field(default=15, alias="WEB_FETCH_TIMEOUT_S")
    """单次 fetch 整体超时（秒，含下载 + 正文抽取）。"""

    web_fetch_max_bytes: int = Field(default=2_097_152, alias="WEB_FETCH_MAX_BYTES")
    """响应体读取上限（字节，默认 2MB）。流式读到上限即停，防大文件吃内存。"""

    web_fetch_max_chars: int = Field(default=40_000, alias="WEB_FETCH_MAX_CHARS")
    """抽取后正文字符上限（默认 4 万字符 ≈ 一篇长公告）。超出截断并标 truncated。"""

    web_fetch_max_concurrency: int = Field(default=4, alias="WEB_FETCH_MAX_CONCURRENCY")
    """同时在飞的 fetch 数上限，治理思路同 web_search。"""

    # --- cn_market A股市场级归因数据（直连东财/同花顺，配方源自 a-stock-data） ---
    cn_market_timeout_s: int = Field(default=15, alias="CN_MARKET_TIMEOUT_S")
    """单请求超时（秒）。实测各端点正常 <5s，超时多半是源站反爬/网络问题。"""

    cn_market_min_interval_s: float = Field(default=1.0, alias="CN_MARKET_MIN_INTERVAL_S")
    """同 host 两次请求最小间隔（秒），外加 0.1~0.5s 随机抖动——防封铁律，
    源站触发阈值约 >5 次/秒（参考 a-stock-data 实测）。"""

    cn_market_cache_ttl_s: int = Field(default=60, alias="CN_MARKET_CACHE_TTL_S")
    """进程内缓存 TTL（秒）。快讯/板块榜分钟级更新，60s 挡住 analyst fan-out
    同一轮重复打源站；响应带 fetched_at，fresh 语义不破。"""

    constituent_snapshot_indices: str = Field(default="", alias="CONSTITUENT_SNAPSHOT_INDICES")
    """每日成分快照追踪的指数代码，逗号分隔（如 ``000300,000905``）。空=禁用调度
    （ADR-0053 阶段 C 向前累积:免费源只回当前成分，唯一 PIT 路径是从启用日起每日落库）。
    手动 ``POST /constituents/snapshot`` 不受本项影响。"""

    constituent_snapshot_interval_h: float = Field(
        default=12.0, alias="CONSTITUENT_SNAPSHOT_INTERVAL_H"
    )
    """成分快照调度的检查间隔（小时）。幂等:每轮只补"今天还没快照"的指数，
    <24h 不会重复打源站（省 akshare + 防封），>1 轮/天纯为重启后尽快补当天。"""


@lru_cache(maxsize=1)
def get_data_settings() -> DataSettings:
    return DataSettings()  # type: ignore[call-arg]
