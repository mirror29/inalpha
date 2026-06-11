"""自定义因子适配器（D-12 · 因子发现 L1）：registered 表达式 → 第五因子源。

从 :mod:`custom_registry` 同步读已注册表达式，求值走 :mod:`expression` 的递归
解释器。注册即生产：进 catalog / timing / score / snapshot 去相关，零额外代码路径。
"""
from __future__ import annotations

import logging

import pandas as pd

from .. import custom_registry
from ..expression import evaluate
from .base import FactorSpec

logger = logging.getLogger(__name__)


class CustomAdapter:
    """registered 自定义表达式因子源（注册表为空时 = 无因子，照常可用）。"""

    source = "custom"

    def available(self) -> bool:
        return True

    def specs(self) -> list[FactorSpec]:
        return [r.spec for r in custom_registry.get_registered()]

    def compute(
        self, df: pd.DataFrame, factor_ids: list[str] | None = None
    ) -> dict[str, pd.Series]:
        if df.empty:
            return {}
        want = set(factor_ids) if factor_ids is not None else None
        out: dict[str, pd.Series] = {}
        for reg in custom_registry.get_registered():
            fid = reg.spec.factor_id
            if want is not None and fid not in want:
                continue
            try:
                out[fid] = evaluate(reg.parsed, df)
            except Exception as exc:  # 单因子求值失败不拖累其他（ADR-0043 D5 同纪律）
                logger.warning("custom factor %s evaluate failed: %r", fid, exc)
        return out
