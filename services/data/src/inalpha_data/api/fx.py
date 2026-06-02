"""``GET /fx`` —— 汇率查询，D-11 加（给 paper 跨币种 cash / equity 折算用）。

设计动机：

paper 模拟盘账户可同时持有不同市场的标的（BTC/USDT、AAPL、sh.600519），计价货币
不同。``/accounts/me`` 把总权益折算到 base currency 时需要汇率。本端点把"取汇率"
职责放在 data 服务（统一 freshness 处理），复用 yfinance forex pair（零 key）。

``rate`` 语义：1 单位 ``base`` 值多少 ``quote``。如 ``base=CNY&quote=USD`` → rate≈0.14
（1 CNY ≈ 0.14 USD）；paper 侧 ``value_base = amount_currency × rate``。

取值优先级：

1. ``base == quote`` → 1.0（``identity``），无网络
2. 两边都是 USD 等价稳定币（USD / USDT / USDC）→ 1.0（``stablecoin``），无网络
3. yfinance forex pair ``{base}{quote}=X``（如 ``CNYUSD=X``）实时价（``yfinance``）

yfinance 拿不到（退市 / 网络 / 未知货币对）→ 抛 ``FX_UNAVAILABLE``（502）。**不**静态
兜底真实汇率（汇率会漂移，乱猜比报错更危险）；由 caller 决定降级——paper equity 折算
遇此把该币种排除 + 显式 warning。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from inalpha_shared.auth import User, get_current_user
from inalpha_shared.errors import InalphaError

from ..connectors import TickerCapable, get_connector_for_venue
from ..schemas import FxQuery, FxResponse

router = APIRouter(tags=["fx"])

# FX 日内波动小且非交易时段不更新，新鲜阈值放宽到 1 小时
STALE_THRESHOLD_SECONDS = 3600

# USD 等价稳定币：互相折算视为 1.0（模拟盘忽略脱锚风险）。
# BUSD：Binance/Paxos 已于 2024 年初下架，保留仅作向后兼容（真实持有以 1:1 处理可能
# 掩盖无法流通的风险，但模拟盘可接受此简化）。与 paper/fx.py 的 _STABLE_USD 保持一致。
_STABLE_USD: frozenset[str] = frozenset({"USD", "USDT", "USDC", "BUSD", "DAI"})


class FxUnavailableError(InalphaError):
    code = "FX_UNAVAILABLE"
    status_code = 502


@router.get("/fx", response_model=FxResponse)
async def get_fx(
    _user: Annotated[User, Depends(get_current_user)],
    query: Annotated[FxQuery, Depends()],
) -> FxResponse:
    """返回 ``base`` → ``quote`` 的汇率（1 base = rate quote）。"""
    now = datetime.now(UTC)
    base = query.base.strip().upper()
    quote = query.quote.strip().upper()

    # 1. 同币种
    if base == quote:
        return FxResponse(
            base=base, quote=quote, rate=1.0, ts=now,
            source="identity", is_stale=False, stale_seconds=0,
        )

    # 2. USD 等价稳定币互转
    if base in _STABLE_USD and quote in _STABLE_USD:
        return FxResponse(
            base=base, quote=quote, rate=1.0, ts=now,
            source="stablecoin", is_stale=False, stale_seconds=0,
        )

    # 3. yfinance forex pair
    connector = get_connector_for_venue("yfinance")
    if not isinstance(connector, TickerCapable):  # 防御：注册表异常
        raise FxUnavailableError(
            "yfinance connector not available for FX",
            details={"base": base, "quote": quote},
        )
    fx_symbol = f"{base}{quote}=X"
    try:
        ts, rate = await connector.fetch_ticker(fx_symbol)
    except Exception as e:
        raise FxUnavailableError(
            f"failed to fetch FX {base}/{quote} ({fx_symbol}): {e}",
            details={"base": base, "quote": quote, "symbol": fx_symbol},
        ) from e

    stale_seconds = max(int((now - ts).total_seconds()), 0)
    return FxResponse(
        base=base,
        quote=quote,
        rate=rate,
        ts=ts,
        source="yfinance",
        is_stale=stale_seconds > STALE_THRESHOLD_SECONDS,
        stale_seconds=stale_seconds,
    )
