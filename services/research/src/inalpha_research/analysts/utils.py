"""Analyst 共享工具 —— 跨 analyst / persona 复用的纯函数。

抽这里是为了消除复制粘贴：``valuation`` 与各 persona analyst 之前各自维护了一份
**逐字节相同**的 web 搜索渲染逻辑，web 搜索字段一变就得改多处、漏改即静默跑旧格式。
"""
from __future__ import annotations

#: fundamentals 快照的指标 → 英文标签（valuation / fundamental / persona 共用）。
#: 英文而非中文：prompt 面向全球资产 + LLM 内部消费，中文 label 会把检索/推理
#: 偏向中文市场视角（CLAUDE.md §3）。
_FINANCIAL_LABELS = {
    "market_cap": "market_cap",
    "pe_ratio": "PE ratio",
    "pb_ratio": "PB ratio",
    "roe": "ROE",
    "gross_margin": "gross margin",
    "net_margin": "net margin",
    "revenue_yoy": "revenue YoY",
    "profit_yoy": "profit YoY",
    "debt_to_equity": "debt/equity",
}
#: 需按百分比渲染的指标 key。
_FINANCIAL_PCT_KEYS = frozenset(
    {"roe", "revenue_yoy", "profit_yoy", "gross_margin", "net_margin", "debt_to_equity"}
)


def render_financial_indicators(
    data: dict,
    *,
    label: str,
    unavailable_hint: str,
    footer: str,
) -> str:
    """把 fundamentals 快照渲染成喂给 LLM 的指标块。

    valuation / persona 等多处共用同一套 labels + 格式化（百分比 / 市值亿·万亿 / 两位小数），
    只在**标题 / 不可用提示 / 末尾指引**三处定制——抽这里避免加新指标时改多处 drift。

    Args:
        data: ``DataClient.get_fundamentals`` 返回的 dict（含 available / indicators / reason）。
        label: 块标题前缀（如 ``"valuation_inputs"`` / ``"fundamentals"``）。
        unavailable_hint: 数据不可用时追加的一句提示（各调用方定制）。
        footer: 指标块末尾的判断指引（各调用方定制）。

    Returns:
        多行字符串；``available`` 为 False 时返回不可用提示块。
    """
    if not data.get("available"):
        return f"{label}: (not available — {data.get('reason', 'unknown')})\n{unavailable_hint}"
    ind = data.get("indicators", {})
    # 数据 as_of 进块头（D-12 时效性红线）：LLM 必须能看到这份财报快照"多新"，
    # 不带日期的数字会被当成"现在"引用。
    data_as_of = data.get("as_of")
    suffix = f", data as_of {data_as_of}" if data_as_of else ""
    lines = [f"{label} (most recent disclosure{suffix}):"]
    for key, lbl in _FINANCIAL_LABELS.items():
        val = ind.get(key)
        if val is None:
            continue
        if key in _FINANCIAL_PCT_KEYS:
            lines.append(f"  {lbl}: {val * 100:.1f}%")
        elif key == "market_cap":
            if val >= 1e12:
                lines.append(f"  {lbl}: {val / 1e12:.2f}T")
            elif val >= 1e9:
                lines.append(f"  {lbl}: {val / 1e9:.2f}B")
            elif val >= 1e6:
                # 100M-1B 中小盘：补 M 档，否则裸浮点 LLM 难推理（CR #86）
                lines.append(f"  {lbl}: {val / 1e6:.2f}M")
            else:
                lines.append(f"  {lbl}: {val:.0f}")
        else:
            lines.append(f"  {lbl}: {val:.2f}")
    lines.append("")
    lines.append(footer)
    return "\n".join(lines)


def render_web_results(results: list[dict]) -> str:
    """把 web 搜索结果渲染成喂给 LLM 的定性背景块（不当硬数字）。

    取前 3 条，标题截断 100 字、摘要截断 200 字。空结果返空串。

    Args:
        results: ``DataClient.get_web_search`` 返回的 dict 列表（含 title / snippet）。

    Returns:
        多行字符串（末尾带换行）；``results`` 为空时返 ``""``。
    """
    if not results:
        return ""
    lines = ["web_search_results (qualitative context only, NOT hard numbers):"]
    for r in results[:3]:
        title = r.get("title", "")[:100]
        snippet = r.get("snippet", "")[:200]
        lines.append(f"  - {title}")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines) + "\n"


def fundamentals_route(*, venue: str, market_type: str) -> str | None:
    """研究 venue → fundamentals 数据源 venue 的映射。

    data 服务 ``/fundamentals`` 只支持 baostock / yfinance，直接透传研究 venue
    （alpaca / binance / ...）会 422 → 永远 unavailable——美股走 alpaca 研究时
    本可从 yfinance 拿到财报却拿不到，这是 fundamental/valuation/persona 一直
    "无 live 数据"的根因之一。

    Returns:
        ``"baostock"`` / ``"yfinance"``，或 ``None``（crypto 无财报，调用方应跳过
        fundamentals 请求省一次 round-trip，转而依赖链上 / web 证据）。
    """
    if market_type == "crypto":
        return None
    v = venue.lower()
    if v in ("baostock", "yfinance"):
        return v
    if v == "akshare" and market_type == "cn_stock":
        return "baostock"
    if market_type == "cn_stock":
        return "baostock"
    if market_type == "hk_stock":
        return "yfinance"
    # us_stock / global_stock / 指数：yfinance 全球兜底（查不到时上游返 available=False）
    return "yfinance"
