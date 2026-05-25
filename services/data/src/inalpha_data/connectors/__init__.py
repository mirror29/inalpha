"""外部市场 / 经纪商接入。

注册表模式（``_base.Connector`` Protocol + venue → connector dict）让 ``api`` 层
按 venue 找 connector，不必关心后端是 CCXT / alpaca-py / akshare。

D-9 起：

- ``binance``  ：CCXT spot OHLCV（crypto）
- ``alpaca``   ：alpaca-py IEX free feed（美股 OHLCV）
- ``akshare``  ：akshare 公开页（A股 sh./sz. + 港股 hk.）
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
