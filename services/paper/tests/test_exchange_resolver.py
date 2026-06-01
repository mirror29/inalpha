"""``resolve_calendar_code`` 单测：各市场 venue+symbol → 正确 calendar code。"""
from __future__ import annotations

import pytest

from inalpha_paper.execution.risk_rules.exchange_resolver import resolve_calendar_code


@pytest.mark.parametrize(
    ("venue", "symbol", "expected"),
    [
        # akshare 前缀（sz 复用 XSHG）
        ("akshare", "sh.600519", "XSHG"),
        ("akshare", "sz.000001", "XSHG"),
        ("akshare", "hk.00700", "XHKG"),
        ("akshare", "jp.6758", "XTKS"),
        ("akshare", "uk.VOD", "XLON"),
        ("akshare", "de.BMW", "XFRA"),
        ("akshare", "weird", None),  # 无前缀 → 未知
        # yfinance / alpaca 美股（无后缀）
        ("yfinance", "AAPL", "XNYS"),
        ("yfinance", "aapl", "XNYS"),  # 大小写不敏感
        ("alpaca", "TSLA", "XNYS"),
        # yfinance 后缀（注意 .to vs .t 不互相误截）
        ("yfinance", "7203.T", "XTKS"),
        ("yfinance", "005930.KS", "XKRX"),
        ("yfinance", "BHP.AX", "XASX"),
        ("yfinance", "RELIANCE.NS", "XBOM"),
        ("yfinance", "VOD.L", "XLON"),
        ("yfinance", "BMW.DE", "XFRA"),
        ("yfinance", "MC.PA", "XPAR"),
        ("yfinance", "RY.TO", "XTSE"),
        ("yfinance", "PETR4.SA", "BVMF"),
        # 全球指数
        ("yfinance", "^GSPC", "XNYS"),
        ("yfinance", "^N225", "XTKS"),
        ("yfinance", "^HSI", "XHKG"),
        ("yfinance", "^GDAXI", "XFRA"),
        # crypto / fred / 未识别 → None
        ("binance", "BTC/USDT", None),
        ("coinbase", "ETH/USD", None),
        ("fred", "DFF", None),
        ("yfinance", "^UNKNOWNIDX", None),  # 未列入指数 → fail-open
        ("someweirdvenue", "XYZ", None),
    ],
)
def test_resolve_calendar_code(venue: str, symbol: str, expected: str | None) -> None:
    assert resolve_calendar_code(venue, symbol) == expected
