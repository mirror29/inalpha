"""Analyst 共享工具 —— 跨 analyst / persona 复用的纯函数。

抽这里是为了消除复制粘贴：``valuation`` 与各 persona analyst 之前各自维护了一份
**逐字节相同**的 web 搜索渲染逻辑，web 搜索字段一变就得改多处、漏改即静默跑旧格式。
"""
from __future__ import annotations


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
