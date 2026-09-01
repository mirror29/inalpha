"""冻结 bars 的稳定内容哈希。"""
from __future__ import annotations

import hashlib
import struct

from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.market_evaluation import MarketEvaluationContext
from inalpha_paper.model.data import Bar


def bars_content_hash(
    bars: list[Bar],
    instrument: InstrumentId,
    context: MarketEvaluationContext,
) -> str:
    """按市场身份、时间戳和 OHLCV 的规范二进制编码计算 SHA-256。"""
    digest = hashlib.sha256()
    identity = f"e2-bars-v2\0{instrument.venue}\0{instrument.symbol}\0{context.canonical_timeframe}\0"
    digest.update(identity.encode())
    for bar in bars:
        digest.update(
            struct.pack(
                "!qq5d",
                bar.bar_open_at,
                bar.ts_event,
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            )
        )
    return digest.hexdigest()


__all__ = ["bars_content_hash"]
