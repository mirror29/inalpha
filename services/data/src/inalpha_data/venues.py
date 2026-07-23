"""数据服务的 venue 与 symbol 规范化。

``akshare`` 曾同时承载多个市场。A 股行情现已迁移到 ``baostock``；过渡期仍接受
旧客户端发送的 ``akshare``，但 connector 路由和持久化统一使用 canonical market identity。
"""

from __future__ import annotations

LEGACY_A_SHARE_VENUE = "akshare"
A_SHARE_VENUE = "baostock"
_A_SHARE_PREFIXES = frozenset({"sh", "sz"})


def canonicalize_market_identity(venue: str, symbol: str) -> tuple[str, str]:
    """返回用于 connector 和持久化的 canonical ``(venue, symbol)``。"""
    normalized_venue = venue.strip().lower()
    normalized_symbol = _canonicalize_a_share_symbol(symbol)
    if normalized_symbol is not None and normalized_venue in {
        LEGACY_A_SHARE_VENUE,
        A_SHARE_VENUE,
    }:
        return A_SHARE_VENUE, normalized_symbol
    return normalized_venue, symbol.strip()


def canonicalize_venue(venue: str, symbol: str) -> str:
    """返回用于 connector 与持久化的 canonical venue。"""
    return canonicalize_market_identity(venue, symbol)[0]


def is_legacy_a_share_venue(venue: str, symbol: str) -> bool:
    """旧 A 股 venue 是否被映射到 ``baostock``。"""
    normalized_venue, _ = canonicalize_market_identity(venue, symbol)
    return venue.strip().lower() == LEGACY_A_SHARE_VENUE and normalized_venue == A_SHARE_VENUE


def _canonicalize_a_share_symbol(symbol: str) -> str | None:
    """把 ``SH.600519`` / ``600519.SH`` 归一为 ``sh.600519``。"""
    normalized = symbol.strip()
    if "." not in normalized:
        return None

    prefix, code = normalized.split(".", 1)
    if prefix.lower() in _A_SHARE_PREFIXES and code.isdigit() and len(code) == 6:
        return f"{prefix.lower()}.{code}"

    code, suffix = normalized.rsplit(".", 1)
    if suffix.lower() in _A_SHARE_PREFIXES and code.isdigit() and len(code) == 6:
        return f"{suffix.lower()}.{code}"
    return None
