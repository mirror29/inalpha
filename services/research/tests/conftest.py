"""pytest fixture：测试 secret + auth headers + bar fixture + FakeLLM."""
from __future__ import annotations

import os
import time
from typing import Any

import jwt
import pytest
from inalpha_shared.config import get_settings

from inalpha_research.config import get_research_settings
from inalpha_research.llm.client import FakeLLMClient

TEST_JWT_SECRET = "test-secret-do-not-use-in-prod-please-and-thank-you"


def make_test_token(sub: str = "test-user", email: str = "t@e.st") -> str:
    return jwt.encode(
        {"sub": sub, "email": email, "exp": int(time.time()) + 3600},
        TEST_JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {make_test_token()}"}


@pytest.fixture(scope="session", autouse=True)
def _ensure_env() -> None:
    os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)
    os.environ.setdefault("DATA_SERVICE_URL", "http://data-mock.test")
    # research 本身不连 DB（D-8b），但 inalpha_shared.Settings 要求 DATABASE_URL
    # 字段非空，给个占位避免 Pydantic 校验炸
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://x:x@localhost:5433/inalpha",
    )
    # 把默认 LLM provider 切成 fake，避免任何测试不小心打到真 LLM
    os.environ.setdefault("LLM_PROVIDER", "fake")
    os.environ.setdefault("LLM_API_KEY", "test-key-not-used-by-fake")
    get_settings.cache_clear()
    get_research_settings.cache_clear()


def make_bar_row(ts_iso: str, close: float = 100.0) -> dict[str, Any]:
    """合成 data-service 风格的 BarResponse JSON 行。"""
    return {
        "ts": ts_iso,
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1.0,
    }


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    """5 analyst + manager 全套预设。

    ``FakeLLMClient`` 按 system prompt 子串匹配，**key 必须是唯一锚定的**：

    - 不能用 ``"technical analyst"``  ── fundamental.py 里写了
      "(the technical analyst handles that)" 会误中
    - 不能用 ``"macro analyst"``      ── fundamental.py opening 是
      "You are a fundamental / macro analyst" 会误中
    - 都用 ``"You are a X"`` 全开头前缀，互相不交叉
    """
    return FakeLLMClient(
        {
            "you are a technical analyst": {
                "stance": "bullish",
                "confidence": 0.7,
                "summary": "20-bar SMA upcrossed 50-bar; RSI 58 not overbought.",
                "key_points": ["SMA20 > SMA50", "RSI 58", "5-bar +3.2%"],
            },
            "you are a fundamental / macro analyst": {
                "stance": "neutral",
                "confidence": 0.5,
                "summary": "Macro environment mixed, halving tailwind partly priced in.",
                "key_points": ["halving priced", "rate-cut delays"],
            },
            "you are a sentiment analyst": {
                "stance": "bullish",
                "confidence": 0.6,
                "summary": "FNG 22 (Extreme Fear) — contrarian bullish bias.",
                "key_points": ["FNG=22", "30d avg 35", "sustained fear 5d"],
            },
            "you are a risk analyst": {
                "stance": "neutral",
                "confidence": 0.55,
                "summary": "ATR 2.1%, DD 9% — normal vol band, no fragility.",
                "key_points": ["ATR/close 2.1%", "max_dd 9%", "vol z 0.3"],
            },
            "you are a macro analyst": {
                "stance": "neutral",
                "confidence": 0.5,
                "summary": "FOMC in 4 weeks; calendar light near-term.",
                "key_points": ["no imminent FOMC", "post-CPI window"],
            },
            "you are a research manager": {
                "rating": "overweight",
                "confidence": 0.65,
                "thesis": (
                    "Technicals show clean upcross + room before overbought; "
                    "macro is neutral but with a halving tailwind. Net positive bias."
                ),
                "risks": [
                    "If RSI > 70 quickly, mean reversion likely",
                    "Macro risk if rate cuts get postponed further",
                ],
                "suggested_action": "open_long 0.02 with stop below SMA50",
                "horizon": "swing",
            },
        }
    )
