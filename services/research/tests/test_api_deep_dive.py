"""``POST /deep_dive`` API 集成测试 —— monkeypatch build_llm_client 走 FakeLLMClient."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import respx
from fastapi.testclient import TestClient
from httpx import Response

from inalpha_research.llm.client import FakeLLMClient

from .conftest import make_bar_row

pytestmark = pytest.mark.integration


def _as_of() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def fake_llm_singleton() -> FakeLLMClient:
    """整次测试共享同一个 fake instance，便于断言 .calls。"""
    return FakeLLMClient(
        {
            "technical analyst": {
                "stance": "bullish",
                "confidence": 0.7,
                "summary": "T",
                "key_points": ["sma cross"],
            },
            "fundamental": {
                "stance": "neutral",
                "confidence": 0.4,
                "summary": "M",
                "key_points": ["macro mixed"],
            },
            "research manager": {
                "rating": "overweight",
                "confidence": 0.6,
                "thesis": "tech bullish, macro neutral; net positive bias",
                "risks": ["fast RSI overbought"],
                "suggested_action": "open_long 0.02",
                "horizon": "swing",
            },
        }
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, fake_llm_singleton: FakeLLMClient) -> TestClient:
    """把 build_llm_client 替成返 fake_llm_singleton；不真打 DeepSeek。"""
    from inalpha_research.api import deep_dive as deep_dive_module

    def _build_fake(**_: Any) -> FakeLLMClient:
        return fake_llm_singleton

    monkeypatch.setattr(deep_dive_module, "build_llm_client", _build_fake)

    from inalpha_research.main import app

    return TestClient(app)


# ────────────────────────────────────────────────────────────────────
# auth
# ────────────────────────────────────────────────────────────────────


def test_deep_dive_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/deep_dive",
        json={
            "venue": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "as_of": _as_of().isoformat(),
            "lookback_days": 7,
        },
    )
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


# ────────────────────────────────────────────────────────────────────
# happy path
# ────────────────────────────────────────────────────────────────────


@respx.mock
def test_deep_dive_returns_research_plan(
    client: TestClient,
    auth_headers: dict[str, str],
    fake_llm_singleton: FakeLLMClient,
) -> None:
    bars = [
        make_bar_row((_as_of() - timedelta(hours=60 - i)).isoformat(), close=100 + i * 0.1)
        for i in range(60)
    ]
    respx.get("http://data-mock.test/bars").mock(return_value=Response(200, json=bars))

    r = client.post(
        "/deep_dive",
        headers=auth_headers,
        json={
            "venue": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "as_of": _as_of().isoformat(),
            "lookback_days": 7,
            "user_question": "should I buy BTC?",
        },
    )
    assert r.status_code == 200, r.json()

    body = r.json()
    assert body["symbol"] == "BTC/USDT"
    assert body["rating"] == "overweight"
    assert body["confidence"] == 0.6
    assert body["suggested_action"] == "open_long 0.02"
    assert body["horizon"] == "swing"
    # 2 个 analyst brief 都该在响应里
    analysts = {b["analyst"] for b in body["briefs"]}
    assert analysts == {"technical", "fundamental"}

    # LLM 共 3 次（2 analyst + 1 manager）
    assert len(fake_llm_singleton.calls) == 3


# ────────────────────────────────────────────────────────────────────
# 数据源故障
# ────────────────────────────────────────────────────────────────────


@respx.mock
def test_deep_dive_continues_when_data_service_500(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """data-service 挂了 → technical 失败但 fundamental 仍能跑 → manager 综合。"""
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(500, json={"code": "DB_DOWN", "message": "down"})
    )

    r = client.post(
        "/deep_dive",
        headers=auth_headers,
        json={
            "venue": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "as_of": _as_of().isoformat(),
            "lookback_days": 7,
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    # technical brief 应该是 placeholder
    technical = next(b for b in body["briefs"] if b["analyst"] == "technical")
    assert technical["confidence"] == 0.0
    assert technical["summary"].startswith("(analyst failed)")


# ────────────────────────────────────────────────────────────────────
# 输入校验
# ────────────────────────────────────────────────────────────────────


def test_deep_dive_rejects_missing_symbol(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/deep_dive",
        headers=auth_headers,
        json={
            "venue": "binance",
            # missing symbol
            "timeframe": "1h",
            "as_of": _as_of().isoformat(),
        },
    )
    # FastAPI / Pydantic 校验返 422 由 install_error_handler 翻成 400 + VALIDATION_ERROR
    assert r.status_code in (400, 422)
