"""Analyst Protocol —— 所有 analyst 实现的最小契约。

设计要点：

- analyst 拿 LLM client + data client 作为构造参数（DI），方便测试注入 fake
- ``run()`` 返 ``AnalystBrief``（pydantic 校验过的），调用方不需要 try/except
  自己再 parse JSON
- 提示词分两段：``system`` 是稳定角色定义（cache 友好，ADR-0014），
  ``user`` 是带 context 的动态部分
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ..data_client import DataClient
from ..llm.client import LLMClient
from ..schemas import AnalystBrief


class Analyst(ABC):
    """所有 analyst 的基类。"""

    #: analyst 类型字符串（落在 ``AnalystBrief.analyst``）。子类必须 override。
    type_id: str = ""

    def __init__(self, *, llm: LLMClient, data: DataClient) -> None:
        if not self.type_id:
            raise NotImplementedError(f"{type(self).__name__}: type_id must be set")
        self._llm = llm
        self._data = data

    # ─── 公共入口 ───

    async def run(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        lookback_days: int,
    ) -> AnalystBrief:
        """跑一次研究。子类只需实现 ``build_user_prompt`` + ``system_prompt``。"""
        system = self.system_prompt()
        user = await self.build_user_prompt(
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            lookback_days=lookback_days,
        )
        raw = await self._llm.complete_json(system=system, user=user)
        return self._parse(raw)

    # ─── 子类 hook ───

    @abstractmethod
    def system_prompt(self) -> str:
        """返回该 analyst 的 system role prompt（稳定，cache 友好）。"""

    @abstractmethod
    async def build_user_prompt(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        lookback_days: int,
    ) -> str:
        """构造 user prompt —— 子类按需调 ``self._data.get_bars`` 拉数据再喂进去。"""

    # ─── 内部 ───

    def _parse(self, raw: dict[str, Any]) -> AnalystBrief:
        """LLM 原始 JSON → ``AnalystBrief``。容错：缺字段给默认值。"""
        payload: dict[str, Any] = {
            "analyst": self.type_id,
            "stance": raw.get("stance", "neutral"),
            "confidence": float(raw.get("confidence", 0.5)),
            "summary": str(raw.get("summary", "")).strip() or "(no summary)",
            "key_points": [str(p) for p in (raw.get("key_points") or [])][:5],
            "raw_excerpt": json.dumps(raw, ensure_ascii=False)[:500],
        }
        return AnalystBrief.model_validate(payload)
