"""Analyst Protocol —— 所有 analyst 实现的最小契约。

设计要点：

- analyst 拿 LLM client + data client 作为构造参数（DI），方便测试注入 fake
- ``run()`` 返 ``AnalystBrief``（pydantic 校验过的），调用方不需要 try/except
  自己再 parse JSON
- 提示词分两段：``system`` 是稳定角色定义（cache 友好，ADR-0014），
  ``user`` 是带 context 的动态部分
- D-13 · P0：新增 ``shared`` 可选参数——runner 预拉 K 线/基本面/因子快照后注入，
  避免 6 个 analyst 各自调 DataClient 拉同一批数据（往返 ×N → 去重为 1 次）。
  ``shared`` 为 None 时回退到 analyst 自己调 DataClient（向后兼容）。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from inalpha_shared import get_logger

from ..data_client import DataClient
from ..factor_client import FactorClient
from ..llm.client import LLMClient
from ..schemas import AnalystBrief

_logger = get_logger(__name__)


@dataclass(frozen=True)
class AnalystContext:
    """Runner 预拉的共享数据——一次拉取，所有 analyst 复用。

    为 None 的字段表示该数据未预拉（analyst 可以自己调 DataClient 回退）。
    """
    bars: list[dict[str, Any]] | None = None
    fundamentals: list[dict[str, Any]] | None = None
    factor_snapshot: list[dict[str, Any]] | None = None


class Analyst(ABC):
    """所有 analyst 的基类。"""

    #: analyst 类型字符串（落在 ``AnalystBrief.analyst``）。子类必须 override。
    type_id: str = ""

    def __init__(
        self,
        *,
        llm: LLMClient,
        data: DataClient,
        factor: FactorClient | None = None,
        shared: AnalystContext | None = None,
    ) -> None:
        if not self.type_id:
            raise NotImplementedError(f"{type(self).__name__}: type_id must be set")
        self._llm = llm
        self._data = data
        # 接现成因子库（docs/miro/11）：technical analyst 用它取有效因子快照；
        # None 或服务不可用时降级回各 analyst 自带的指标计算。
        self._factor = factor
        # D-13 · P0：共享预拉数据——为 None 时 analyst 照旧自己拉。
        self._shared = shared
        # confidence 硬上限（D-12 双档纪律）：子类在 build_user_prompt 里按"本次
        # 拿到 live 数据与否"设值（如 fundamental 0.75/0.55、macro 0.7/0.5），
        # run() 在 parse 后代码级 clamp——prompt 里写 cap 只是软约束，LLM 不一定守。
        self._confidence_cap: float | None = None

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
        brief = self._parse(raw)
        # 注意顺序：cap 由 build_user_prompt 按数据可得性设置，必须在其之后读
        if self._confidence_cap is not None:
            brief.confidence = min(brief.confidence, self._confidence_cap)
        return brief

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
        """构造 user prompt —— 子类按需调 ``self._data.get_bars`` 或从
        ``self._shared`` 读预拉数据。"""

    # ─── 内部 ───

    def _parse(self, raw: dict[str, Any]) -> AnalystBrief:
        """LLM 原始 JSON → ``AnalystBrief``。容错：缺字段给默认值。

        D-8c 起增加 factors 解析：每个 factor 校验失败被丢弃（不阻断），
        让旧 LLM 响应（无 factors 字段）依然能进 schema。

        D-8b' review B2 fix：confidence clamp 到 [0, 1]、stance fallback 到
        "neutral"（旧实现 LLM 返 1.5 / "bull" 这种非 enum 会让 pydantic 抛 →
        整条 deep_dive 链路 500，但 manager 声称"兜底不抛"——这里把锅兜住）。

        D-13 · P1：静默降级变可见——每次 normalize/clamp/drop 都打 warning log。
        """
        raw_stance = raw.get("stance")
        raw_confidence = raw.get("confidence")
        stance = _normalize_stance(raw_stance)
        confidence = _clamp_unit(raw_confidence)

        # D-13 · P1：静默降级变可见——LLM 返了非标准值就打 warning，方便排查。
        if raw_stance is not None and str(raw_stance).strip().lower() != stance:
            _logger.warning(
                "stance_normalized",
                analyst=self.type_id,
                raw=raw_stance,
                normalized=stance,
            )
        if raw_confidence is not None:
            try:
                rc = float(raw_confidence)
                if rc < 0 or rc > 1:
                    _logger.warning(
                        "confidence_clamped",
                        analyst=self.type_id,
                        raw=rc,
                        clamped=confidence,
                    )
            except (TypeError, ValueError):
                _logger.warning(
                    "confidence_not_numeric",
                    analyst=self.type_id,
                    raw=raw_confidence,
                    default=confidence,
                )

        raw_factors = raw.get("factors")
        parsed_factors = _safe_parse_factors(raw_factors)
        if isinstance(raw_factors, list) and len(parsed_factors) < len(raw_factors):
            _logger.warning(
                "factors_dropped",
                analyst=self.type_id,
                raw_count=len(raw_factors),
                parsed_count=len(parsed_factors),
            )

        payload: dict[str, Any] = {
            "analyst": self.type_id,
            "stance": stance,
            "confidence": confidence,
            "summary": str(raw.get("summary", "")).strip() or "(no summary)",
            "key_points": [str(p) for p in (raw.get("key_points") or [])][:5],
            "factors": parsed_factors,
            "raw_excerpt": json.dumps(raw, ensure_ascii=False)[:500],
        }
        return AnalystBrief.model_validate(payload)


def _normalize_stance(v: Any) -> str:
    """LLM 输出非 enum stance（如 "bull" / "very-bullish" / null）兜底 neutral。"""
    s = str(v).strip().lower() if v is not None else ""
    if s in ("bullish", "bearish", "neutral"):
        return s
    return "neutral"


def _clamp_unit(v: Any, default: float = 0.5) -> float:
    """confidence clamp 到 [0, 1]；非数值兜底 0.5。"""
    try:
        x = float(v) if v is not None else default
    except (TypeError, ValueError):
        return default
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _safe_parse_factors(raw_factors: Any) -> list[dict[str, Any]]:
    """逐项校验 factor dict，跳过格式不合法的 —— 容错优先于完整性。"""
    from ..schemas import Factor

    if not isinstance(raw_factors, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw_factors:
        if not isinstance(item, dict):
            continue
        try:
            out.append(Factor.model_validate(item).model_dump(mode="json"))
        except Exception:
            continue
    return out[:4]  # 上限 4 个，避免 LLM 失控喷洒
