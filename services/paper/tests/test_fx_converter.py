"""``fx.BaseCurrencyConverter`` + ``fx.needs_network`` 测试（D-11）。

不需要 DB / 网络：本地可解析的币种走 1.0，跨币种用 fake DataClient 注入汇率。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from inalpha_paper.data_client import DataServiceError
from inalpha_paper.fx import BaseCurrencyConverter, needs_network

pytestmark = pytest.mark.anyio


class _FakeDataClient:
    """记录 get_fx 调用次数 + 按 (base,quote) 返预设响应或抛错。"""

    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]] | None = None,
                 raise_for: set[tuple[str, str]] | None = None) -> None:
        self.responses = responses or {}
        self.raise_for = raise_for or set()
        self.calls: list[tuple[str, str]] = []

    async def get_fx(self, *, base: str, quote: str) -> dict[str, Any]:
        self.calls.append((base, quote))
        if (base, quote) in self.raise_for:
            raise DataServiceError("fx down", code="FX_UNAVAILABLE")
        return self.responses[(base, quote)]


async def test_local_rates_no_network() -> None:
    """同币种 / USD 稳定币本地解析，零网络调用。"""
    dc = _FakeDataClient()
    conv = BaseCurrencyConverter("USD", dc)  # type: ignore[arg-type]
    assert await conv.convert(Decimal("100"), "USD") == Decimal("100")
    assert await conv.convert(Decimal("100"), "USDT") == Decimal("100")
    assert await conv.convert(Decimal("100"), "USDC") == Decimal("100")
    assert dc.calls == []  # 本地解析，没打网络
    assert conv.warnings == []


async def test_cross_currency_via_network() -> None:
    """跨币种调 /fx，缓存：同币种只查一次。"""
    dc = _FakeDataClient(
        responses={
            ("CNY", "USD"): {"rate": 0.14, "is_stale": False, "stale_seconds": 0},
        }
    )
    conv = BaseCurrencyConverter("USD", dc)  # type: ignore[arg-type]
    assert await conv.convert(Decimal("100"), "CNY") == Decimal("100") * Decimal("0.14")
    # 再查一次 CNY 应命中缓存，不重复打网络
    await conv.convert(Decimal("50"), "CNY")
    assert dc.calls == [("CNY", "USD")]
    assert conv.warnings == []


async def test_fx_unavailable_excludes_and_warns() -> None:
    """FX 拿不到 → convert 返 None（caller 排除该币种）+ 记 warning。"""
    dc = _FakeDataClient(raise_for={("CNY", "USD")})
    conv = BaseCurrencyConverter("USD", dc)  # type: ignore[arg-type]
    assert await conv.convert(Decimal("100"), "CNY") is None
    assert len(conv.warnings) == 1
    assert "CNY" in conv.warnings[0]


async def test_stale_fx_used_but_warns() -> None:
    """stale 汇率仍用于折算，但附 warning。"""
    dc = _FakeDataClient(
        responses={("JPY", "USD"): {"rate": 0.0064, "is_stale": True, "stale_seconds": 7200}},
    )
    conv = BaseCurrencyConverter("USD", dc)  # type: ignore[arg-type]
    result = await conv.convert(Decimal("10000"), "JPY")
    assert result == Decimal("10000") * Decimal("0.0064")
    assert len(conv.warnings) == 1
    assert "JPY" in conv.warnings[0]


async def test_no_data_client_warns_for_nonlocal() -> None:
    """dc=None 时非本地币种无法折算 → None + warning；本地币种照常。"""
    conv = BaseCurrencyConverter("USD", None)
    assert await conv.convert(Decimal("100"), "USD") == Decimal("100")
    assert await conv.convert(Decimal("100"), "CNY") is None
    assert len(conv.warnings) == 1


def test_needs_network() -> None:
    assert needs_network(["USD"], "USD") is False
    assert needs_network(["USD", "USDT"], "USD") is False  # 稳定币本地
    assert needs_network(["USD", "CNY"], "USD") is True
    assert needs_network([], "USD") is False
