"""venue 与 symbol 规范化单元测试。"""

from inalpha_data.venues import (
    canonicalize_market_identity,
    canonicalize_venue,
    is_legacy_a_share_venue,
)


def test_canonicalize_legacy_a_share_prefix() -> None:
    assert canonicalize_market_identity(" AKShare ", " SH.600519 ") == (
        "baostock",
        "sh.600519",
    )


def test_canonicalize_legacy_a_share_yahoo_suffix() -> None:
    assert canonicalize_market_identity("akshare", "600519.SH") == (
        "baostock",
        "sh.600519",
    )
    assert canonicalize_market_identity("akshare", "000001.sz") == (
        "baostock",
        "sz.000001",
    )


def test_canonicalize_current_a_share_symbol() -> None:
    assert canonicalize_market_identity("BAOSTOCK", "600519.SH") == (
        "baostock",
        "sh.600519",
    )


def test_non_a_share_identity_only_normalizes_venue_whitespace() -> None:
    assert canonicalize_market_identity(" YFINANCE ", " 0700.HK ") == (
        "yfinance",
        "0700.HK",
    )


def test_malformed_a_share_symbols_are_not_canonicalized() -> None:
    for symbol in ("sh.12345", "sh.1234567", "sh.ABCDEF", "sh.123.456", "12345.SH"):
        assert canonicalize_market_identity("baostock", symbol) == ("baostock", symbol)


def test_legacy_alias_requires_a_share_symbol() -> None:
    assert canonicalize_venue("akshare", "hk.00700") == "akshare"
    assert is_legacy_a_share_venue("akshare", "sh.600519") is True
    assert is_legacy_a_share_venue("baostock", "sh.600519") is False
