"""``execution.currency_resolver.resolve_currency`` 的纯函数测试（D-11）。"""
from __future__ import annotations

import pytest

from inalpha_paper.execution.currency_resolver import resolve_currency


@pytest.mark.parametrize(
    ("venue", "symbol", "expected"),
    [
        # crypto：取 symbol 的 quote
        ("binance", "BTC/USDT", "USDT"),
        ("binance", "ETH/USDC", "USDC"),
        ("coinbase", "BTC/USD", "USD"),
        ("binance", "BTCUSDT", "USDT"),  # 无 / → 默认 USDT
        # 美股 / yfinance 无后缀 → USD
        ("yfinance", "AAPL", "USD"),
        ("alpaca", "TSLA", "USD"),
        # A股 canonical venue + 迁移前旧值
        ("baostock", "sh.600519", "CNY"),
        ("baostock", "sz.000001", "CNY"),
        ("akshare", "sh.600519", "CNY"),
        # 旧 akshare 全球前缀只为已持久化记录保留
        ("akshare", "hk.00700", "HKD"),
        # 全球单股（yfinance 后缀）
        ("yfinance", "005930.KS", "KRW"),
        ("yfinance", "7203.T", "JPY"),
        ("yfinance", "VOD.L", "GBP"),
        ("yfinance", "BHP.AX", "AUD"),
        # 全球指数
        ("yfinance", "^GSPC", "USD"),
        ("yfinance", "^N225", "JPY"),
        ("yfinance", "^HSI", "HKD"),
    ],
)
def test_resolve_currency(venue: str, symbol: str, expected: str) -> None:
    assert resolve_currency(venue, symbol) == expected


def test_unidentified_falls_back_to_default() -> None:
    """fred / 未识别 venue / 未知标的 → fail-open 返 default。"""
    assert resolve_currency("fred", "DFF", default="USD") == "USD"
    assert resolve_currency("fred", "DFF", default="EUR") == "EUR"
    assert resolve_currency("mystery-venue", "FOO", default="USD") == "USD"
    # yfinance 未列入的指数 → fail-open default
    assert resolve_currency("yfinance", "^UNKNOWN", default="USD") == "USD"


def test_case_insensitive() -> None:
    assert resolve_currency("BINANCE", "btc/usdt") == "USDT"
    assert resolve_currency("AKShare", "SH.600519") == "CNY"
