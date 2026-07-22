"""数据服务 venue 规范化。

``akshare`` 曾同时承载多个市场。A 股行情现已迁移到 ``baostock``；过渡期仍接受
旧客户端发送的 ``akshare`` + ``sh./sz.``，但所有存储和 connector 路由统一使用新 venue。
"""
from __future__ import annotations

LEGACY_A_SHARE_VENUE = "akshare"
A_SHARE_VENUE = "baostock"
_A_SHARE_PREFIXES = ("sh.", "sz.")


def canonicalize_venue(venue: str, symbol: str) -> str:
    """返回用于 connector 与持久化的 canonical venue。"""
    normalized = venue.strip().lower()
    if normalized == LEGACY_A_SHARE_VENUE and symbol.strip().lower().startswith(_A_SHARE_PREFIXES):
        return A_SHARE_VENUE
    return normalized


def is_legacy_a_share_venue(venue: str, symbol: str) -> bool:
    """旧 A 股 venue 是否被映射到 ``baostock``。"""
    return venue.strip().lower() == LEGACY_A_SHARE_VENUE and canonicalize_venue(
        venue, symbol
    ) == A_SHARE_VENUE
