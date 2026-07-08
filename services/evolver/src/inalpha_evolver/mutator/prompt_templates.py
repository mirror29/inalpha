"""LLM 变异 prompt 模板。

``SYSTEM_PROMPT`` 是静态模板（~5KB，cacheable）。
``build_user_prompt`` 每轮不同（含当前源码 + 回测报告 + 变异 hint），动态生成。
"""
from __future__ import annotations

import json
from typing import Any, Final

# ── 系统提示词（静态，cacheable）────────────────────────────────────────────
SYSTEM_PROMPT: Final[str] = """# 策略变异助手

你是 Inalpha 量化交易平台的策略变异引擎。你的任务是：对给定的 Python 策略源码，
生成 **unified diff** 格式的代码变更，提升策略的回测表现。

## 输出格式

你**必须**输出标准的 unified diff（``diff -u`` 风格）：

```
--- a/strategy.py
+++ b/strategy.py
@@ -10,7 +10,7 @@
 ...
```

- 始终包含 ``--- a/strategy.py`` 和 ``+++ b/strategy.py`` 头
- 每段 hunk 至少包含 **3 行 context**
- diff 只包含**必要变更**，不要无意义的格式调整
- 如果认为当前策略无法改进，输出空 diff（仅含 header 无 hunk）

## 变异原则

1. **渐进改进** —— 单次变异只改 1-3 处，不要重写整个策略
2. **参数调优** —— 调整周期参数（均线/阈值）、仓位大小等
3. **逻辑增强** —— 加过滤器（成交量确认、波动率过滤）、止损逻辑
4. **遵守框架** —— 只使用已导入的内核符号（Strategy, Bar, OrderSide, OrderType, etc.）
5. **不要引入** —— 文件 IO、网络请求、eval/exec、第三方库 import

## 框架 API

可用的符号（无需 import）：

- ``Strategy`` —— 基类，必须继承
- ``Bar`` —— OHLCV + timestamp
- ``Order``, ``OrderSide``（BUY / SELL）, ``OrderType``（MARKET / LIMIT）
- ``ClientOrderId``, ``InstrumentId``, ``StrategyId``
- ``Clock``, ``MessageBus``
- 事件类：``OrderSubmitted``, ``OrderAccepted``, ``OrderFilled``, ``OrderRejected``, ``OrderCanceled``, ``PositionOpened``, ``PositionChanged``, ``PositionClosed``
- ``deque``, ``uuid4`` —— 已注入 namespace

## 回测指标说明

- Sharpe Ratio：年化夏普，>1.0 合格，>2.0 优秀
- Calmar Ratio：年化收益/最大回撤，>1.0 合格
- Max Drawdown：最大回撤百分比，<20% 合格，<10% 优秀
- Total Return：累计收益百分比
- Num Trades：总交易次数（太少=信号稀疏，太多=过拟合）
- Sortino Ratio：仅考虑下行风险的夏普
"""


def _fmt(report: dict[str, Any], key: str, default: str = "N/A") -> str:
    """安全取值，None / 缺字段 → default。"""
    v = report.get(key)
    if v is None:
        return default
    return str(v)


def _fmt_pct(report: dict[str, Any], key: str, default: str = "N/A") -> str:
    """取值并格式化为百分比（None → default）。"""
    v = report.get(key)
    if v is None:
        return default
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


def _format_report(report: dict[str, Any] | None) -> str:
    """将 BacktestReport dict 格式化为可读的摘要。"""
    if not report:
        return "（无回测数据）"
    lines = [
        f"夏普比率(Sharpe):     {_fmt(report, 'sharpe')}",
        f"索提诺比率(Sortino):  {_fmt(report, 'sortino')}",
        f"卡玛比率(Calmar):     {_fmt(report, 'calmar')}",
        f"最大回撤:             {_fmt_pct(report, 'max_drawdown_pct')}",
        f"累计收益:             {_fmt_pct(report, 'total_return_pct')}",
        f"交易次数:             {_fmt(report, 'num_trades')}",
        f"处理 Bar 数:          {_fmt(report, 'num_bars_processed')}",
        f"年化波动率:           {_fmt(report, 'volatility')}",
        f"胜率:                 {_fmt(report, 'win_rate')}",
    ]
    return "\n".join(lines)


def build_user_prompt(
    current_source: str,
    report: dict[str, Any] | None = None,
    hint: str = "",
) -> str:
    """构建本轮变异的 user prompt。

    Args:
        current_source: 当前策略的完整源码字符串。
        report: 当前策略的 ``BacktestReport`` dict（含回测指标）。
        hint: 变异方向提示，如 "降低回撤"、"增加交易频率"。

    Returns:
        格式化的 user prompt。
    """
    parts: list[str] = [
        "# 当前策略源码",
        "",
        "```python",
        current_source,
        "```",
        "",
    ]

    if report:
        parts.extend([
            "# 当前回测指标",
            "",
            "```",
            _format_report(report),
            "```",
            "",
        ])

    if hint:
        parts.extend([
            "# 变异方向",
            "",
            hint,
            "",
        ])

    parts.append("请生成 unified diff 改进上述策略。只输出 diff，不要额外说明。")

    return "\n".join(parts)


def build_user_prompt_from_report_json(
    current_source: str,
    report_json: str,
    hint: str = "",
) -> str:
    """从 JSON 字符串构建 user prompt（方便 API 层序列化）。"""
    try:
        report = json.loads(report_json)
    except (json.JSONDecodeError, TypeError):
        report = None
    return build_user_prompt(current_source, report, hint)