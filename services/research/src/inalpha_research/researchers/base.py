"""Bull/Bear 共同基类 —— 输入 analyst briefs + 辩论 history，输出本轮发言文本。

跟 ``analysts.Analyst`` 的区别：

- analyst 输出**结构化 JSON**（stance / confidence / factors），喂给 manager
- researcher 输出**自由文本**（一段对喷论证），追加进 ``debate_log``；manager
  最终读全文做综合

因此 researcher 的 ``LLMClient`` 调用走 ``complete_text``（不存在），暂用
``complete_json`` 但 prompt 让 LLM 只放一个 ``{"argument": "..."}``——避免给
``LLMClient`` Protocol 加新方法引发的耦合扩散。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from ..llm.client import LLMClient
from ..schemas import AnalystBrief, DebateTurn

#: 资产类型 —— 细到能让 prompt 切换"对的术语"，避免在 crypto 上谈 P/E 或在 A 股上谈 halving。
AssetType = Literal["crypto", "us_stock", "cn_stock", "hk_stock", "global_stock"]


class Researcher(ABC):
    """Bull / Bear researcher 共同接口。"""

    #: 角色字符串，落进 ``DebateTurn.role``。子类必须 override。
    role: Literal["bull", "bear"] = "bull"

    def __init__(self, *, llm: LLMClient) -> None:
        self._llm = llm

    @abstractmethod
    def system_prompt(self, *, asset_type: AssetType) -> str:
        """子类返回自己的 system prompt；``asset_type`` 由 venue + symbol 推断。"""

    async def speak(
        self,
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        as_of: datetime,
        briefs: list[AnalystBrief],
        history: list[DebateTurn],
        round_no: int,
    ) -> str:
        """跑一轮发言。返回 LLM 给出的论证文本。"""
        asset_type = infer_asset_type(venue=venue, symbol=symbol)
        system = self.system_prompt(asset_type=asset_type)
        user = _format_user_prompt(
            role=self.role,
            venue=venue,
            symbol=symbol,
            timeframe=timeframe,
            as_of=as_of,
            briefs=briefs,
            history=history,
            round_no=round_no,
        )
        raw = await self._llm.complete_json(system=system, user=user)
        argument = str(raw.get("argument", "")).strip()
        if not argument:
            argument = "(empty argument from LLM)"
        return argument


#: crypto 交易所识别集合 —— 在 services/data 的 binance/okx/... 加入 CCXT
#: 时同步扩这里。
_CRYPTO_VENUES = frozenset(
    {"binance", "okx", "bybit", "coinbase", "kraken", "huobi", "bitfinex", "bitstamp", "gate"}
)

#: yfinance 后缀 → 资产类型映射；未列出的统一归 ``global_stock``。
#: ``""``（无后缀）→ 美股；``^``（指数）→ ``global_stock`` 由调用方处理
_YF_SUFFIX_TO_TYPE: dict[str, AssetType] = {
    ".KS": "global_stock",   # 韩国 KOSPI
    ".KQ": "global_stock",   # 韩国 KOSDAQ
    ".T": "global_stock",    # 日本东证
    ".HK": "hk_stock",       # 港股（yfinance 标识）
    ".SS": "cn_stock",       # 上证
    ".SZ": "cn_stock",       # 深证
    ".L": "global_stock",    # 伦交所
    ".AX": "global_stock",   # ASX
    ".NS": "global_stock",   # 印度 NSE
    ".BO": "global_stock",   # 印度 BSE
    ".TO": "global_stock",   # 多伦多
    ".SA": "global_stock",   # 巴西
    ".PA": "global_stock",   # 巴黎
    ".DE": "global_stock",   # 法兰克福
    ".F": "global_stock",    # 法兰克福备用
}


def infer_asset_type(*, venue: str, symbol: str = "") -> AssetType:
    """venue + symbol 二维推断资产类型。

    优先级：crypto venue > venue 显式映射 > symbol 后缀（yfinance 用）> 兜底 ``global_stock``。

    例：

    - ``binance`` + ``BTC/USDT`` → ``crypto``
    - ``alpaca`` + ``AAPL``      → ``us_stock``
    - ``akshare`` + ``sh.600519`` → ``cn_stock``
    - ``akshare`` + ``hk.00700``  → ``hk_stock``
    - ``akshare`` + ``jp.6758``   → ``global_stock``（日股目前归全球桶）
    - ``yfinance`` + ``005930.KS`` → ``global_stock``
    - ``yfinance`` + ``AAPL``      → ``us_stock``（无后缀视为美股）
    - ``yfinance`` + ``^N225``     → ``global_stock``（指数）
    """
    v = venue.lower()
    if v in _CRYPTO_VENUES:
        return "crypto"

    if v == "alpaca":
        return "us_stock"

    if v == "akshare":
        s = symbol.lower()
        if s.startswith(("sh.", "sz.")):
            return "cn_stock"
        if s.startswith("hk."):
            return "hk_stock"
        # jp / uk / de 等
        return "global_stock"

    if v == "yfinance":
        if symbol.startswith("^"):
            return "global_stock"  # 指数
        if "." not in symbol:
            return "us_stock"  # 无后缀：AAPL / MSFT / SPY
        # 找后缀
        for suffix, atype in _YF_SUFFIX_TO_TYPE.items():
            if symbol.endswith(suffix):
                return atype
        return "global_stock"

    return "global_stock"


def fundamental_note_for(asset_type: AssetType) -> str:
    """给 bull/bear/fundamental prompt 用：返回该资产类型的"基本面术语锚点"。

    让 LLM 在 prompt 框定的范围内选词——美股谈 10-K，A股谈年报，crypto 谈 on-chain。
    """
    if asset_type == "crypto":
        return (
            "Asset fundamentals (crypto): on-chain flows, supply schedule / unlocks, "
            "halving cycle phase, exchange reserves, stablecoin liquidity, narrative "
            "adoption (L2 / RWA / etc.)"
        )
    if asset_type == "us_stock":
        return (
            "Company fundamentals (US equity): 10-K / 10-Q segment revenue, EPS / "
            "forward guidance, gross margin trend, FCF, share buyback / dilution, "
            "analyst consensus revisions"
        )
    if asset_type == "cn_stock":
        return (
            "Company fundamentals (A-share): 年报 / 季报 ROE / 毛利率 / 净利率, sector "
            "policy (industrial / regulatory), retail flow vs 北向资金, supply chain "
            "exposure to USD / commodity prices"
        )
    if asset_type == "hk_stock":
        return (
            "Company fundamentals (HK equity): interim / annual report, Southbound 资金 "
            "flow, dual-listing arbitrage vs A-share, regulatory exposure (HKMA / mainland), "
            "USD-peg / HKD-rates linkage"
        )
    # global_stock fallback
    return (
        "Company fundamentals (global equity): annual / interim report, segment growth, "
        "FX exposure, local regulatory / policy context, sector-relative valuation"
    )


def _format_user_prompt(
    *,
    role: Literal["bull", "bear"],
    venue: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    briefs: list[AnalystBrief],
    history: list[DebateTurn],
    round_no: int,
) -> str:
    """渲染给 Bull/Bear 的 user prompt。

    包含：标的 + as_of + 5 个 analyst brief 摘要 + 完整辩论 history +
    对方上一轮发言（如有）。
    """
    parts: list[str] = [
        "⚠️ TIME ANCHOR — read before anything else:",
        f"  as_of = {as_of.isoformat()} — this is the REAL wall-clock NOW.",
        "  Your training cutoff is earlier than as_of. Any event your training "
        "data labels as 'upcoming' that has a date < as_of is already HISTORY — "
        "do NOT write '即将' / 'upcoming' / 'next week' about it.",
        "  Do NOT cite specific calendar dates (CPI prints, FOMC meetings, "
        "earnings, halvings, elections, etc.) UNLESS that exact date appears in "
        "an analyst_brief below. If you need to reference an event that's not in "
        "the briefs, say 'the briefs do not cover this' instead of guessing.",
        "",
        f"asset: {symbol} @ {venue}",
        f"timeframe: {timeframe}",
        f"debate_round: {round_no}",
        f"your_role: {role}",
        "",
        "analyst_briefs:",
    ]
    for b in briefs:
        kp = "; ".join(b.key_points[:3]) if b.key_points else "(no key points)"
        parts.append(
            f"  [{b.analyst}] stance={b.stance} conf={b.confidence:.2f} "
            f"summary={b.summary} | top_points: {kp}"
        )

    if history:
        parts.append("")
        parts.append("debate_history (oldest first):")
        for turn in history:
            parts.append(f"  Round {turn.round} {turn.role.upper()}: {turn.content}")
        last_opponent = next(
            (t for t in reversed(history) if t.role != role),
            None,
        )
        if last_opponent is not None:
            parts.append("")
            parts.append(
                f"opponent_last_turn (rebut this directly!): {last_opponent.content}"
            )
    else:
        parts.append("")
        parts.append("debate_history: (this is the first turn — make your opening case)")

    parts.append("")
    parts.append(
        'Output ONLY a JSON object: {"argument": "<your full argument as one paragraph, '
        '180-280 words>"}. Do not add any other top-level keys.'
    )
    return "\n".join(parts)
