"""run 创建请求的规范化与幂等摘要。"""
from __future__ import annotations

import hashlib
import json
import struct
from datetime import UTC, datetime
from typing import Any

from .schemas import StartRunRequest

_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def normalized_request(request: StartRunRequest) -> tuple[dict[str, Any], str]:
    payload = request.model_dump(mode="json")
    config = payload["config"]
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return config, digest


def approval_request_digest(request: StartRunRequest) -> str:
    """Compute the cross-language digest covered by the Ed25519 capability."""
    config = request.config
    canonical = [
        request.seed_strategy_id,
        _number_text(request.budget),
        config.venue,
        config.symbol,
        config.timeframe,
        _number_text(_epoch_milliseconds(config.from_ts)),
        _number_text(_epoch_milliseconds(config.as_of)),
        _float64_hex(config.initial_cash),
        _float64_hex(config.fee_rate),
        _float64_hex(config.validation_split),
        config.trading_mode,
        _number_text(config.leverage),
        request.llm.config_digest,
    ]
    return hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _number_text(value: int) -> str:
    return str(value)


def _float64_hex(value: float) -> str:
    return struct.pack(">d", value).hex()


def _epoch_milliseconds(value: datetime) -> int:
    delta = value - _UNIX_EPOCH
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


__all__ = ["approval_request_digest", "normalized_request"]
