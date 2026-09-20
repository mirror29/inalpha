"""Authoritative market-symbol to underlying asset identity resolution."""

from __future__ import annotations

from dataclasses import dataclass

from .venues import canonicalize_market_identity

_CRYPTO_VENUES = frozenset(
    {"binance", "kraken", "coinbase", "okx", "bybit", "bitget", "kucoin"}
)
_CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH")


@dataclass(frozen=True, slots=True)
class AssetIdentity:
    """Stable asset identity and legacy event code emitted by Data only."""

    asset_id: str
    venue: str
    symbol: str
    event_asset_code: str


def resolve_asset_identity(venue: str, symbol: str) -> AssetIdentity:
    """Resolve one canonical underlying without leaking parsing into consumers."""
    canonical_venue, canonical_symbol = canonicalize_market_identity(venue, symbol)
    normalized = canonical_symbol.strip().upper().split(":", 1)[0].replace("-", "/")
    if canonical_venue in _CRYPTO_VENUES:
        if "/" in normalized:
            base = normalized.split("/", 1)[0]
        else:
            base = next(
                (
                    normalized[: -len(quote)]
                    for quote in _CRYPTO_QUOTES
                    if normalized.endswith(quote) and len(normalized) > len(quote)
                ),
                normalized,
            )
        return AssetIdentity(
            asset_id=asset_id_from_event_code(base),
            venue=canonical_venue,
            symbol=canonical_symbol,
            event_asset_code=base,
        )
    if canonical_venue == "baostock":
        code = canonical_symbol.lower()
        return AssetIdentity(
            asset_id=asset_id_from_event_code(code.upper()),
            venue=canonical_venue,
            symbol=canonical_symbol,
            event_asset_code=code.upper(),
        )
    code = normalized.replace("/", ":")
    return AssetIdentity(
        asset_id=asset_id_from_event_code(code),
        venue=canonical_venue,
        symbol=canonical_symbol,
        event_asset_code=code,
    )


def asset_id_from_event_code(value: str) -> str:
    """Map a normalized event code to the stable cross-service asset identifier."""
    code = value.strip().upper()
    return f"asset:{code}"


__all__ = ["AssetIdentity", "asset_id_from_event_code", "resolve_asset_identity"]
