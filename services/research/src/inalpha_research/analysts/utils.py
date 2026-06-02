"""Analyst 共享工具 —— 跨 analyst / persona 复用的纯函数。

抽这里是为了消除复制粘贴：``valuation`` 与各 persona analyst 之前各自维护了一份
**逐字节相同**的 web 搜索渲染逻辑，web 搜索字段一变就得改多处、漏改即静默跑旧格式。
"""
from __future__ import annotations

#: fundamentals 快照的指标 → 中文标签（valuation / persona 共用，避免多处 drift）。
_FINANCIAL_LABELS = {
    "market_cap": "市值",
    "pe_ratio": "市盈率 PE",
    "pb_ratio": "市净率 PB",
    "roe": "ROE",
    "gross_margin": "毛利率",
    "net_margin": "净利率",
    "revenue_yoy": "营收同比",
    "profit_yoy": "利润同比",
    "debt_to_equity": "负债权益比",
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
    lines = [f"{label} (most recent disclosure):"]
    for key, lbl in _FINANCIAL_LABELS.items():
        val = ind.get(key)
        if val is None:
            continue
        if key in _FINANCIAL_PCT_KEYS:
            lines.append(f"  {lbl}: {val * 100:.1f}%")
        elif key == "market_cap":
            if val > 1e12:
                lines.append(f"  {lbl}: {val / 1e12:.1f}万亿")
            elif val > 1e8:
                lines.append(f"  {lbl}: {val / 1e8:.1f}亿")
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
