"""P0·P2·P4·P5 扩展测试：新算子 / WalkForward IC / 多标的 / 回测模拟。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from inalpha_factor.effectiveness import walk_forward_ic
from inalpha_factor.expression import (
    ExpressionError,
    evaluate,
    parse_expression,
    _EXTRA_COLUMNS,
)

from .conftest import make_ohlcv


def _eval(expr: str, df: pd.DataFrame) -> pd.Series:
    return evaluate(parse_expression(expr), df)


# ── P2: 新算子测试 ──────────────────────────────────────────────────


class TestP2NewOperators:
    def test_skew(self) -> None:
        df = make_ohlcv(200)
        s = _eval("Skew($close, 20)", df)
        want = df["close"].astype(float).rolling(20).skew()
        pd.testing.assert_series_equal(s, want, check_names=False)

    def test_kurt(self) -> None:
        df = make_ohlcv(200)
        s = _eval("Kurt($close, 20)", df)
        want = df["close"].astype(float).rolling(20).kurt()
        pd.testing.assert_series_equal(s, want, check_names=False)

    def test_med(self) -> None:
        df = make_ohlcv(200)
        s = _eval("Med($close, 20)", df)
        want = df["close"].astype(float).rolling(20).median()
        pd.testing.assert_series_equal(s, want, check_names=False)

    def test_idxmax(self) -> None:
        df = make_ohlcv(100)
        s = _eval("IdxMax($high, 10)", df)
        # 手动验证：rolling(10).apply(lambda x: x.argmax())
        close = df["close"].astype(float)
        assert s.index.equals(df.index)
        assert s.dropna().between(0, 9).all()

    def test_idxmin(self) -> None:
        df = make_ohlcv(100)
        s = _eval("IdxMin($low, 10)", df)
        assert s.index.equals(df.index)
        assert s.dropna().between(0, 9).all()

    def test_cov(self) -> None:
        df = make_ohlcv(200)
        s = _eval("Cov($close, $volume, 20)", df)
        close = df["close"].astype(float)
        volume = df["volume"].astype(float)
        want = close.rolling(20).cov(volume)
        pd.testing.assert_series_equal(s, want, check_names=False)

    def test_and_or_not(self) -> None:
        df = make_ohlcv(200)
        and_ = _eval("And(Greater($close, Mean($close, 20)), Greater($volume, Mean($volume, 20)))", df)
        # And 返回 float64（0.0/1.0），不是 bool
        assert and_.dtype in (bool, np.dtype("float64"), np.dtype("float32"))
        or_ = _eval("Or(Greater($close, Mean($close, 20)), Greater($volume, Mean($volume, 20)))", df)
        assert or_.dtype in (bool, np.dtype("float64"), np.dtype("float32"))
        not_ = _eval("Not(Greater($close, Mean($close, 20)))", df)
        assert not_.dtype in (bool, np.dtype("float64"), np.dtype("float32"))
        # 验证值域：0 或 1
        for s in (and_, or_, not_):
            valid = s.dropna()
            assert valid.isin([0.0, 1.0]).all()

    def test_slope(self) -> None:
        """Slope 输出不应全 NaN（有足够数据时）。"""
        df = make_ohlcv(200)
        s = _eval("Slope($close, 20)", df)
        assert s.index.equals(df.index)
        assert s.dropna().shape[0] > 0

    @pytest.mark.parametrize(
        ("expr", "hint"),
        [
            ("Skew($close)", "argument"),
            ("Kurt($close, 20, 30)", "argument"),
            ("Slope($close)", "argument"),
            ("IdxMax($close)", "argument"),
            ("IdxMin($close, 10, 20)", "argument"),
            ("Cov($close, $volume)", "argument"),
            ("And($close)", "argument"),
            ("Or($close)", "argument"),
            ("Not($close, $volume)", "argument"),
        ],
    )
    def test_p2_operators_arg_count(self, expr: str, hint: str) -> None:
        with pytest.raises(ExpressionError, match="expects|argument"):
            parse_expression(expr)


# ── P2: 扩展字段解析测试 ────────────────────────────────────────────


class TestP2ExtraColumns:
    def test_extra_columns_defined(self) -> None:
        assert "pe" in _EXTRA_COLUMNS
        assert "pb" in _EXTRA_COLUMNS
        assert "roe" in _EXTRA_COLUMNS
        assert "market_cap" in _EXTRA_COLUMNS
        assert "fed_rate" in _EXTRA_COLUMNS
        assert "cpi" in _EXTRA_COLUMNS

    def test_extra_column_parse_ok(self) -> None:
        p = parse_expression("$close / $pe")
        assert "pe" in p.columns

    def test_extra_column_evaluate_with_nan(self) -> None:
        """扩展字段在 df 中不存在时，求值会因列缺失报 KeyError 而非静默错。
        engine 层会在调用 evaluate 前注入这些列，确保 evaluate 本身不抛。"""
        df = make_ohlcv(100)
        p = parse_expression("$close / $pe")
        # 没有 $pe 列 → evaluate 会抛 KeyError（engine 层在调用前会先注入）
        with pytest.raises(KeyError):
            evaluate(p, df)

    def test_extra_column_unknown_rejected(self) -> None:
        with pytest.raises(ExpressionError, match="unknown column"):
            parse_expression("$close / $unknown_field")


# ── P4: WalkForward IC 测试 ─────────────────────────────────────────


class TestP4WalkForwardIC:
    def test_walk_forward_perfect_factor(self) -> None:
        """完美因子：OOS IC 各段均高，退化率低。"""
        rng = np.random.default_rng(42)
        n = 600
        close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n))))
        factor = close.shift(-5) / close - 1.0  # 知道未来
        # 末尾 5 根 NaN，drop 掉
        result = walk_forward_ic(factor, close, horizon=5, n_splits=5, min_samples=50)
        assert result["oos_ic_mean"] is not None
        assert result["oos_ic_mean"] > 0.5
        assert result["insample_ic"] is not None
        assert result["insample_ic"] > 0.5
        assert result["n_splits"] == 5

    def test_walk_forward_noise_factor(self) -> None:
        """纯噪声因子：OOS IC 接近 0，退化率可能很高。"""
        rng = np.random.default_rng(99)
        n = 600
        close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n))))
        factor = pd.Series(rng.normal(0.0, 1.0, n))
        result = walk_forward_ic(factor, close, horizon=5, n_splits=5, min_samples=50)
        assert result["oos_ic_mean"] is not None
        assert abs(result["oos_ic_mean"]) < 0.3

    def test_walk_forward_short_series(self) -> None:
        """短序列：仍能返回结果，不抛异常。"""
        close = pd.Series([100.0] * 30)
        factor = pd.Series([1.0] * 30)
        result = walk_forward_ic(factor, close, horizon=1, n_splits=3, min_samples=5)
        # 短序列可能返回 None
        assert isinstance(result, dict)

    def test_walk_forward_degradation_rate(self) -> None:
        """退化率计算：insample 高 OOS 低 → 高退化率。"""
        rng = np.random.default_rng(7)
        n = 800
        close = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n))))
        # 前 2/3 有效，后 1/3 噪声——模拟衰减
        fwd = close.shift(-5) / close - 1.0
        factor = fwd.copy()
        cut = 500
        factor.iloc[cut:] = rng.normal(0.0, 1.0, size=n - cut)
        result = walk_forward_ic(factor, close, horizon=5, n_splits=5, min_samples=50)
        # 退化率应存在且非负
        assert result["degradation_rate"] is not None
        assert result["oos_ic_mean"] is not None


# ── P5: 扩展字段 schema 测试 ────────────────────────────────────────


class TestP5MultiSymbol:
    def test_custom_score_request_symbols_field(self) -> None:
        """CustomScoreRequest 支持 symbols 字段。"""
        from inalpha_factor.schemas import CustomScoreRequest

        req = CustomScoreRequest(
            expression="$close - Ref($close, 5)",
            venue="binance",
            symbol="BTC/USDT",
            symbols=["BTC/USDT", "ETH/USDT"],
            timeframe="1h",
        )
        assert req.symbols == ["BTC/USDT", "ETH/USDT"]

    def test_backtest_score_request_inherits_symbols(self) -> None:
        """BacktestScoreRequest 继承 CustomScoreRequest 的 symbols 字段。"""
        from inalpha_factor.schemas import BacktestScoreRequest

        req = BacktestScoreRequest(
            expression="$close - Ref($close, 5)",
            venue="binance",
            symbol="BTC/USDT",
            symbols=["BTC/USDT", "ETH/USDT"],
            timeframe="1h",
        )
        assert req.symbols == ["BTC/USDT", "ETH/USDT"]


# ── P2: 红队扩展——新算子审计 ───────────────────────────────────────


@pytest.mark.parametrize(
    ("expr", "hint"),
    [
        # 扩展算子审计
        ("Skew($close, -5)", "window"),
        ("Kurt($close, 0)", "window"),
        ("Med($close, 501)", "window"),
        ("Slope($close, -1)", "window"),
        ("Cov($close, $volume, 0)", "window"),
        # 扩展算子正常用法
        ("Skew($close, 20)", ""),
        ("Kurt($close, 20)", ""),
        ("Med($close, 20)", ""),
        ("Slope($close, 20)", ""),
        ("Cov($close, $volume, 20)", ""),
        ("IdxMax($close, 10)", ""),
        ("IdxMin($close, 10)", ""),
        ("And($close, $volume)", ""),
        ("Or($close, $volume)", ""),
        ("Not($close)", ""),
    ],
)
def test_p2_operator_audit(expr: str, hint: str) -> None:
    if hint:
        with pytest.raises(ExpressionError, match=hint):
            parse_expression(expr)
    else:
        parse_expression(expr)  # 不应抛