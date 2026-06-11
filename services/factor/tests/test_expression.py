"""受限因子表达式 DSL 测试（D-12 · 因子发现 L1）：白名单 / 红队 / 求值正确性。"""
from __future__ import annotations

import pandas as pd
import pytest

from inalpha_factor.expression import (
    ExpressionError,
    evaluate,
    parse_expression,
)

from .conftest import make_ohlcv


def _eval(expr: str, df: pd.DataFrame) -> pd.Series:
    return evaluate(parse_expression(expr), df)


# ── 白名单通过用例 ────────────────────────────────────────────────────


def test_roc_expression_matches_manual() -> None:
    """ROC5 表达式 = 手算 close.pct_change(5)。"""
    df = make_ohlcv(100)
    got = _eval("($close - Ref($close, 5)) / Ref($close, 5)", df)
    want = df["close"].astype(float).pct_change(5)
    pd.testing.assert_series_equal(got, want, check_names=False)


def test_rolling_and_rank_operators() -> None:
    df = make_ohlcv(120)
    got = _eval("Mean($close, 20) / Std($close, 20)", df)
    close = df["close"].astype(float)
    want = close.rolling(20).mean() / close.rolling(20).std()
    pd.testing.assert_series_equal(got, want, check_names=False)

    rank = _eval("Rank($volume, 20)", df)
    assert rank.dropna().between(0, 1).all()


def test_if_sign_corr_compose() -> None:
    df = make_ohlcv(150)
    s = _eval("If($close > Mean($close, 20), 1, -1) * Corr($close, $volume, 30)", df)
    assert s.index.equals(df.index)
    assert s.dropna().abs().le(1.0 + 1e-9).all()


def test_scalar_broadcast_and_unary() -> None:
    df = make_ohlcv(50)
    s = _eval("-Delta($close, 1) / 2", df)
    want = -df["close"].astype(float).diff(1) / 2
    pd.testing.assert_series_equal(s, want, check_names=False)


# ── 红队用例（每条都该在解析期被拒）──────────────────────────────────


@pytest.mark.parametrize(
    ("expr", "hint"),
    [
        ("Ref($close, -5)", "lag"),  # 负 lag = 看未来
        ("Ref($close, 0)", "lag"),  # 零 lag 同样拒（语义等价恒等，留着只会混淆）
        ("Delta($close, -1)", "lag"),
        ("Rank($close)", "argument"),  # 无 window 的全样本 Rank = 归一化泄漏
        ("Mean($close, 0)", "window"),
        ("Mean($close, 501)", "window"),  # 超 MAX_WINDOW
        ("Quantile($close, 20, 1.5)", "q must"),
        ("Foo($close, 5)", "unknown operator"),
        ("__import__('os')", "unknown operator"),  # 不在算子白名单，调不到
        ("$close.shift(-1)", "direct operator"),  # 属性调用被拒
        ("(lambda: 1)()", "direct operator"),
        ("$secret_column", "unknown column"),
        ("1 + 2", "no market data column"),  # 常数不是因子
        ("$close if True else $open", "not allowed"),  # IfExp 节点不在白名单
        ("[$close for _ in range(3)]", "not allowed"),
        ("Mean($close, w)", "window"),  # window 必须字面量
        ("f'{$close}'", "syntax"),
    ],
)
def test_red_team_rejected(expr: str, hint: str) -> None:
    with pytest.raises(ExpressionError) as ei:
        parse_expression(expr)
    assert hint.lower() in str(ei.value).lower()


def test_rejects_oversized_expression() -> None:
    with pytest.raises(ExpressionError, match="too long"):
        parse_expression("$close + " * 300 + "$close")


def test_rejects_node_bomb() -> None:
    # 嵌套炸弹：节点数超限（长度未超但复杂度超）
    expr = "$close"
    for _ in range(120):
        expr = f"Abs({expr})"
    with pytest.raises(ExpressionError, match=r"too complex|too long"):
        parse_expression(expr)


def test_no_lookahead_by_construction() -> None:
    """构造性验证：截断未来 bar 不改变历史段的因子值（表达式无未来入口）。"""
    df = make_ohlcv(200)
    expr = "Rank(Delta($close, 1), 30) * Mean($volume, 10)"
    full = _eval(expr, df)
    cut = _eval(expr, df.iloc[:150])
    pd.testing.assert_series_equal(full.iloc[:150], cut, check_names=False)


def test_evaluate_handles_inf_via_division() -> None:
    """除零产生的 inf 不炸求值（由下游 score_factor 统一清理）。"""
    df = make_ohlcv(60).copy()
    s = _eval("$close / Delta($close, 1)", df)  # diff 可能为 0 → inf
    assert len(s) == len(df)
    assert not s.isna().all()


def test_parsed_columns_tracked() -> None:
    p = parse_expression("Corr($close, $volume, 20) + Ref($high, 3)")
    assert p.columns == frozenset({"close", "volume", "high"})
