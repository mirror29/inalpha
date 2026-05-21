"""pytest fixture：测试 secret + auth headers + 一个最小 BarResponse builder。"""
from __future__ import annotations

import os
import time
from typing import Any

import jwt
import pytest
from inalpha_shared.config import get_settings

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
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://quant:devpass@localhost:5433/inalpha",
    )
    os.environ.setdefault("JWT_SECRET", TEST_JWT_SECRET)
    os.environ.setdefault("DATA_SERVICE_URL", "http://data-mock.test")
    get_settings.cache_clear()


def make_bar_row(ts_iso: str, close: float = 100.0) -> dict[str, Any]:
    """合成一行 data-service 风格的 BarResponse JSON。"""
    return {
        "ts": ts_iso,
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1.0,
    }
