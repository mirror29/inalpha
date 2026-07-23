"""外部市场 / 经纪商接入。

注册表模式（``_base.Connector`` Protocol + venue → connector dict）让 ``api`` 层
按 venue 找 connector，不必关心后端是 CCXT / alpaca-py / 腾讯财经 / Baostock。

D-9 起：

- ``binance``  ：CCXT spot OHLCV（crypto）
- ``alpaca``   ：alpaca-py IEX free feed（美股 OHLCV）
- ``baostock`` ：A 股逻辑 venue；腾讯 HTTPS 行情 + Baostock 基本面/日历/成分
"""

from ._base import (
    Connector,
    TickerCapable,
    get_connector_for_venue,
    list_registered_venues,
    register_connector,
    unregister_connector,
)

__all__ = [
    "Connector",
    "TickerCapable",
    "get_connector_for_venue",
    "list_registered_venues",
    "register_connector",
    "unregister_connector",
]
