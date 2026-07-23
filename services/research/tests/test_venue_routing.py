"""research 的市场分类与 fundamentals 路由契约测试。"""
from inalpha_research.analysts.utils import fundamentals_route
from inalpha_research.researchers.base import infer_asset_type


def test_baostock_a_share_routes_to_cn_fundamentals() -> None:
    market_type = infer_asset_type(venue="baostock", symbol="sh.600519")
    assert market_type == "cn_stock"
    assert fundamentals_route(venue="baostock", market_type=market_type) == "baostock"


def test_legacy_akshare_a_share_routes_to_baostock_fundamentals() -> None:
    market_type = infer_asset_type(venue="akshare", symbol="SH.600519")
    assert market_type == "cn_stock"
    assert fundamentals_route(venue="akshare", market_type=market_type) == "baostock"


def test_hk_fundamentals_use_yfinance() -> None:
    market_type = infer_asset_type(venue="yfinance", symbol="0700.HK")
    assert market_type == "hk_stock"
    assert fundamentals_route(venue="yfinance", market_type=market_type) == "yfinance"
