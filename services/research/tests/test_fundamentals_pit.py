"""ADR-0053 阶段 A · research DataClient.get_fundamentals 的 as_of 透传单测。"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from inalpha_research.data_client import DataClient

pytestmark = pytest.mark.anyio


@respx.mock
async def test_get_fundamentals_threads_as_of() -> None:
    """传 as_of → GET /fundamentals 带 as_of 查询参数（防 analyst 读未披露财报）。"""
    route = respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": False, "reason": "pit"})
    )
    async with DataClient("http://data-mock.test", "t") as data:
        await data.get_fundamentals(
            venue="baostock",
            symbol="sh.600519",
            as_of=datetime(2020, 1, 1, tzinfo=UTC),
        )
    assert route.calls.last.request.url.params.get("as_of") == "2020-01-01T00:00:00+00:00"


@respx.mock
async def test_get_fundamentals_omits_as_of_when_none() -> None:
    """不传 as_of → 不带 as_of 参数（研究当下不做 PIT 截断）。"""
    route = respx.get("http://data-mock.test/fundamentals").mock(
        return_value=Response(200, json={"available": True})
    )
    async with DataClient("http://data-mock.test", "t") as data:
        await data.get_fundamentals(venue="baostock", symbol="sh.600519")
    assert "as_of" not in route.calls.last.request.url.params
