"""受限 qlib 风格因子表达式 —— 解析 / 审计 / 求值（D-12 · 因子发现 L1 第一块）。

让 agent/用户用一行表达式定义自定义因子，例如::

    ($close - Ref($close, 5)) / Ref($close, 5)
    Rank($volume, 20) * Sign(Delta($close, 1))

设计要点（安全边界）：

- **白名单 DSL，不是受限 Python**：语法恰好是 Python 表达式子集 → 用
  ``ast.parse(mode="eval")`` 解析，但求值是**自写的递归解释器**走算子白名单映射
  pandas——不存在 eval/exec/任意代码执行面，比策略沙盒（受限 exec）高一个安全档
- **lookahead 三层防线的服务端层**（ADR-0019 关键约定）：
  1. ``Ref``/``Delta`` 第二参必须**正整数字面量**——负数 = 向未来看，解析期拒绝
  2. ``Rank``/``Quantile``/``Mean``/``Std`` 等统计算子**必须带 window 字面量**
     （1..500）——全样本版算子根本不提供，归一化泄漏（用未来分布归一现在）无从发生
  3. 前瞻收益只由服务端 ``effectiveness._forward_return`` 计算（尾部 H 根已丢），
     表达式拿不到任何未来 bar 入口
- 资源上限：表达式 ≤ 2000 字符、AST 节点 ≤ 200、window ≤ 500——纯 pandas 列运算，
  bar 数另由 API 层限制（≤10000），无需进程隔离

列引用用 ``$close / $open / $high / $low / $volume``（qlib 习惯）；解析前做 token
替换（``$`` 不是合法 Python），白名单外的列名解析期拒绝。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

#: 表达式资源上限
MAX_EXPRESSION_LENGTH = 2000
MAX_AST_NODES = 200
MAX_WINDOW = 500

#: 允许的 OHLCV 列（$ 前缀引用）
_COLUMNS = frozenset({"open", "high", "low", "close", "volume"})

_COL_PREFIX = "__col_"
_DOLLAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")

#: 算子白名单：name → (最少参数数, 最多参数数)
#: 语义注释见 _eval_call 的实现（与 qlib 表达式习惯对齐）
_OPERATORS: dict[str, tuple[int, int]] = {
    "Ref": (2, 2),       # Ref(s, n)：n 根 bar 前的值（n 必须正整数字面量）
    "Delta": (2, 2),     # Delta(s, n)：s - Ref(s, n)
    "Mean": (2, 2),      # Mean(s, w)：w 窗滚动均值
    "Std": (2, 2),       # Std(s, w)：w 窗滚动标准差
    "Sum": (2, 2),       # Sum(s, w)
    "Max": (2, 2),       # Max(s, w)：滚动最大
    "Min": (2, 2),       # Min(s, w)
    "EMA": (2, 2),       # EMA(s, w)：指数均线（span=w）
    "WMA": (2, 2),       # WMA(s, w)：线性加权均线
    "Corr": (3, 3),      # Corr(a, b, w)：滚动相关
    "Rank": (2, 2),      # Rank(s, w)：w 窗内百分位排名（0-1）
    "Quantile": (3, 3),  # Quantile(s, w, q)：w 窗滚动分位数（q ∈ (0,1) 字面量）
    "Abs": (1, 1),
    "Log": (1, 1),       # Log(s)：log(|s|)，非正值留 NaN
    "Sign": (1, 1),
    "Greater": (2, 2),   # Greater(a, b)：逐元素 max（qlib 语义）
    "Less": (2, 2),      # Less(a, b)：逐元素 min
    "If": (3, 3),        # If(cond, a, b)：逐元素三元
}

#: 第二参必须正整数字面量的算子（lookahead 防线 1）
_POSITIVE_LAG_OPS = frozenset({"Ref", "Delta"})
#: 末位（或第二位）window 参数必须 1..MAX_WINDOW 字面量的算子（lookahead 防线 2）
_WINDOW_ARG_INDEX: dict[str, int] = {
    "Mean": 1, "Std": 1, "Sum": 1, "Max": 1, "Min": 1,
    "EMA": 1, "WMA": 1, "Rank": 1, "Corr": 2, "Quantile": 1,
}

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_ALLOWED_UNARY = (ast.USub, ast.UAdd)
_ALLOWED_CMPOPS = (ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq)


class ExpressionError(ValueError):
    """表达式解析 / 审计失败（caller 转 4xx，message 给 LLM 改写依据）。"""


@dataclass(frozen=True, slots=True)
class ParsedExpression:
    """通过审计的表达式（持有 AST，eval 时复用，不二次解析）。"""

    raw: str
    tree: ast.Expression
    columns: frozenset[str]  # 引用到的 OHLCV 列


def parse_expression(raw: str) -> ParsedExpression:
    """解析 + 全量审计；任何违例抛 :class:`ExpressionError`（带可改写的原因）。"""
    if not raw or not raw.strip():
        raise ExpressionError("expression is empty")
    if len(raw) > MAX_EXPRESSION_LENGTH:
        raise ExpressionError(
            f"expression too long ({len(raw)} > {MAX_EXPRESSION_LENGTH} chars)"
        )

    columns: set[str] = set()

    def _sub(m: re.Match[str]) -> str:
        col = m.group(1).lower()
        if col not in _COLUMNS:
            raise ExpressionError(
                f"unknown column ${m.group(1)}; allowed: "
                + ", ".join(f"${c}" for c in sorted(_COLUMNS))
            )
        columns.add(col)
        return f"{_COL_PREFIX}{col}"

    py_src = _DOLLAR_RE.sub(_sub, raw)

    try:
        tree = ast.parse(py_src, mode="eval")
    except SyntaxError as e:
        raise ExpressionError(f"syntax error: {e.msg} (at offset {e.offset})") from e

    n_nodes = sum(1 for _ in ast.walk(tree))
    if n_nodes > MAX_AST_NODES:
        raise ExpressionError(f"expression too complex ({n_nodes} > {MAX_AST_NODES} AST nodes)")

    _audit(tree.body)
    if not columns:
        raise ExpressionError(
            "expression references no market data column ($close/$open/...); "
            "a constant expression is not a factor"
        )
    return ParsedExpression(raw=raw, tree=tree, columns=frozenset(columns))


def _audit(node: ast.expr) -> None:
    """递归审计：节点 / 算子 / 字面量约束（lookahead 防线在这里）。"""
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise ExpressionError(f"operator {type(node.op).__name__} not allowed")
        _audit(node.left)
        _audit(node.right)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARY):
            raise ExpressionError(f"unary {type(node.op).__name__} not allowed")
        _audit(node.operand)
        return
    if isinstance(node, ast.Compare):
        if len(node.ops) != 1 or len(node.comparators) != 1:
            raise ExpressionError("chained comparisons not allowed")
        if not isinstance(node.ops[0], _ALLOWED_CMPOPS):
            raise ExpressionError(f"comparison {type(node.ops[0]).__name__} not allowed")
        _audit(node.left)
        _audit(node.comparators[0])
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise ExpressionError(f"literal {node.value!r} not allowed (numbers only)")
        return
    if isinstance(node, ast.Name):
        if not node.id.startswith(_COL_PREFIX):
            raise ExpressionError(
                f"name '{node.id}' not allowed; reference data via $close/$open/... "
                "and functions from the operator whitelist"
            )
        return
    if isinstance(node, ast.Call):
        _audit_call(node)
        return
    raise ExpressionError(f"syntax element {type(node).__name__} not allowed")


def _audit_call(node: ast.Call) -> None:
    if not isinstance(node.func, ast.Name):
        raise ExpressionError("only direct operator calls allowed (no attributes/lambdas)")
    name = node.func.id
    if name not in _OPERATORS:
        raise ExpressionError(
            f"unknown operator {name}; allowed: " + ", ".join(sorted(_OPERATORS))
        )
    if node.keywords:
        raise ExpressionError(f"{name}: keyword arguments not allowed")
    lo, hi = _OPERATORS[name]
    if not (lo <= len(node.args) <= hi):
        raise ExpressionError(f"{name} expects {lo} argument(s), got {len(node.args)}")

    # lookahead 防线 1：Ref/Delta 的 lag 必须正整数字面量（负 lag = 看未来）
    if name in _POSITIVE_LAG_OPS:
        lag = node.args[1]
        if not (isinstance(lag, ast.Constant) and isinstance(lag.value, int)):
            raise ExpressionError(f"{name}: lag must be an integer literal")
        if lag.value <= 0:
            raise ExpressionError(
                f"{name}: lag must be positive (lag={lag.value} would look into the future)"
            )
        if lag.value > MAX_WINDOW:
            raise ExpressionError(f"{name}: lag {lag.value} > {MAX_WINDOW}")

    # lookahead 防线 2：统计/归一算子必须带有界 window 字面量（全样本版不存在）
    widx = _WINDOW_ARG_INDEX.get(name)
    if widx is not None:
        w = node.args[widx]
        if not (isinstance(w, ast.Constant) and isinstance(w.value, int)):
            raise ExpressionError(f"{name}: window must be an integer literal")
        if not (1 <= w.value <= MAX_WINDOW):
            raise ExpressionError(f"{name}: window must be in 1..{MAX_WINDOW}, got {w.value}")

    if name == "Quantile":
        q = node.args[2]
        if not (isinstance(q, ast.Constant) and isinstance(q.value, (int, float))):
            raise ExpressionError("Quantile: q must be a numeric literal")
        if not (0.0 < float(q.value) < 1.0):
            raise ExpressionError(f"Quantile: q must be in (0, 1), got {q.value}")

    for arg in node.args:
        _audit(arg)


# ── 求值（递归解释器，无 eval/exec）─────────────────────────────────────


def evaluate(parsed: ParsedExpression, df: pd.DataFrame) -> pd.Series:
    """在 OHLCV DataFrame 上求值，返回与 df.index 对齐的因子时序。

    df 须含 parsed.columns 的全部列。求值结果若是标量（理论上 parse 已挡住），
    广播为常数序列。inf 由下游 ``score_factor`` 统一清理。
    """
    result = _eval(parsed.tree.body, df)
    if not isinstance(result, pd.Series):
        return pd.Series(float(result), index=df.index)
    return result.astype(float)


def _eval(node: ast.expr, df: pd.DataFrame):
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return df[node.id.removeprefix(_COL_PREFIX)].astype(float)
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, df)
        return -v if isinstance(node.op, ast.USub) else +v
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, df), _eval(node.right, df)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        return left**right  # Pow（audit 已限定五种之一）
    if isinstance(node, ast.Compare):
        left, right = _eval(node.left, df), _eval(node.comparators[0], df)
        op = node.ops[0]
        if isinstance(op, ast.Gt):
            return left > right
        if isinstance(op, ast.GtE):
            return left >= right
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Eq):
            return left == right
        return left != right
    # audit 保证剩下只可能是白名单 Call
    return _eval_call(node, df)  # type: ignore[arg-type]


def _eval_call(node: ast.Call, df: pd.DataFrame):
    name = node.func.id  # type: ignore[union-attr]
    args = [_eval(a, df) for a in node.args]

    def _series(v) -> pd.Series:
        return v if isinstance(v, pd.Series) else pd.Series(float(v), index=df.index)

    if name == "Ref":
        return _series(args[0]).shift(int(args[1]))
    if name == "Delta":
        return _series(args[0]).diff(int(args[1]))
    if name == "Mean":
        return _series(args[0]).rolling(int(args[1])).mean()
    if name == "Std":
        return _series(args[0]).rolling(int(args[1])).std()
    if name == "Sum":
        return _series(args[0]).rolling(int(args[1])).sum()
    if name == "Max":
        return _series(args[0]).rolling(int(args[1])).max()
    if name == "Min":
        return _series(args[0]).rolling(int(args[1])).min()
    if name == "EMA":
        return _series(args[0]).ewm(span=int(args[1]), adjust=False).mean()
    if name == "WMA":
        w = int(args[1])
        weights = np.arange(1, w + 1, dtype=float)
        weights /= weights.sum()
        return (
            _series(args[0])
            .rolling(w)
            .apply(lambda x: float(np.dot(x, weights)), raw=True)
        )
    if name == "Corr":
        return _series(args[0]).rolling(int(args[2])).corr(_series(args[1]))
    if name == "Rank":
        return _series(args[0]).rolling(int(args[1])).rank(pct=True)
    if name == "Quantile":
        return _series(args[0]).rolling(int(args[1])).quantile(float(args[2]))
    if name == "Abs":
        return _series(args[0]).abs()
    if name == "Log":
        s = _series(args[0]).abs()
        return pd.Series(np.log(s.where(s > 0)), index=df.index)
    if name == "Sign":
        return pd.Series(np.sign(_series(args[0])), index=df.index)
    if name == "Greater":
        return pd.Series(np.maximum(_series(args[0]), _series(args[1])), index=df.index)
    if name == "Less":
        return pd.Series(np.minimum(_series(args[0]), _series(args[1])), index=df.index)
    # If（audit 保证三参）
    cond = _series(args[0]).astype(bool)
    return pd.Series(
        np.where(cond, _series(args[1]), _series(args[2])), index=df.index
    )
