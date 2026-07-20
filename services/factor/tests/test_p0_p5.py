"""P0·P2·P4·P5 扩展测试：新算子 / WalkForward IC / 多标的 / 回测模拟。"""
from __future__ import annotations

from typing import Any

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


# ── P5: 多标的并发评估（engine._custom_score_multi）───────────────────


class TestP5MultiSymbolConcurrent:
    """P5 多标的并发评估（engine._custom_score_multi 路径）。

    直接调用 engine.custom_score 并传 symbols 参数，mock _fetch_df 绕开 data-service。
    """

    @pytest.fixture
    def engine(self) -> Any:
        from unittest.mock import AsyncMock

        from inalpha_factor.engine import FactorEngine

        from inalpha_factor.config import get_factor_settings

        settings = get_factor_settings()
        engine = FactorEngine(settings)
        # mock _fetch_df 返回合成数据，不真连 data-service
        async def _fake_fetch_df(*, venue: str, symbol: str, **kwargs: Any) -> pd.DataFrame:
            return make_ohlcv(320, seed=hash(symbol) % 2**31)

        engine._fetch_df = AsyncMock(side_effect=_fake_fetch_df)  # type: ignore[method-assign]
        return engine

    @pytest.fixture
    def engine_partial_fail(self) -> Any:
        """部分标的 _fetch_df 抛异常。"""
        from unittest.mock import AsyncMock

        from inalpha_factor.engine import FactorEngine

        from inalpha_factor.config import get_factor_settings

        engine = FactorEngine(get_factor_settings())
        call_count = 0

        async def _fake(*, venue: str, symbol: str, **kwargs: Any) -> pd.DataFrame:
            nonlocal call_count
            call_count += 1
            if symbol == "FAIL/USDT":
                raise RuntimeError("data-service unavailable")
            return make_ohlcv(320, seed=hash(symbol) % 2**31)

        engine._fetch_df = AsyncMock(side_effect=_fake)  # type: ignore[method-assign]
        return engine

    @pytest.fixture
    def engine_all_fail(self) -> Any:
        """全部标的 _fetch_df 抛异常。"""
        from unittest.mock import AsyncMock

        from inalpha_factor.engine import FactorEngine

        from inalpha_factor.config import get_factor_settings

        engine = FactorEngine(get_factor_settings())

        async def _fake(**kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("service down")

        engine._fetch_df = AsyncMock(side_effect=_fake)  # type: ignore[method-assign]
        return engine

    async def _run(self, engine: Any, symbols: list[str]) -> dict[str, Any]:
        return await engine.custom_score(
            expression="($close - Ref($close, 5)) / Ref($close, 5)",
            name="test_momentum",
            venue="binance",
            symbol="",
            symbols=symbols,
            timeframe="1h",
            as_of=None,
            lookback_bars=720,
            horizon_bars=5,
            quantiles=5,
        )

    @pytest.mark.asyncio
    async def test_multi_symbol_normal(self, engine: Any) -> None:
        """3 个标的正常评估，返回跨品种 IC。"""
        result = await self._run(engine, ["BTC/USDT", "ETH/USDT", "SOL/USDT"])

        assert result["available"] is True
        assert "multi_symbol" in result
        ms = result["multi_symbol"]
        assert ms["n_symbols_evaluated"] == 3
        assert ms["n_symbols_failed"] == 0
        assert ms["cross_symbol_ic_mean"] is not None
        # 每种标的的 per_symbol 都有 rank_ic
        for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            assert sym in ms["per_symbol"]
            assert ms["per_symbol"][sym]["rank_ic"] is not None

    @pytest.mark.asyncio
    async def test_multi_symbol_partial_failure(self, engine_partial_fail: Any) -> None:
        """部分标的 data 不可用，降级不阻断整体评估。"""
        result = await self._run(engine_partial_fail, ["BTC/USDT", "FAIL/USDT", "ETH/USDT"])

        assert result["available"] is True
        ms = result["multi_symbol"]
        assert ms["n_symbols_failed"] == 1
        assert ms["n_symbols_evaluated"] == 2
        # 失败的标的不在 per_symbol 中
        assert "FAIL/USDT" not in ms["per_symbol"]
        assert "BTC/USDT" in ms["per_symbol"]
        assert "ETH/USDT" in ms["per_symbol"]

    @pytest.mark.asyncio
    async def test_multi_symbol_all_failure(self, engine_all_fail: Any) -> None:
        """全部标的失败 → available=false。"""
        result = await self._run(engine_all_fail, ["BTC/USDT", "ETH/USDT"])

        assert result["available"] is False
        ms = result["multi_symbol"]
        assert ms["n_symbols_evaluated"] == 0
        assert ms["n_symbols_failed"] == 2
        assert ms["cross_symbol_ic_mean"] is None

    @pytest.mark.asyncio
    async def test_multi_symbol_consistency_identical(self) -> None:
        """所有标的 IC 相同时，consistency=1.0。"""
        from unittest.mock import AsyncMock

        from inalpha_factor.engine import FactorEngine

        from inalpha_factor.config import get_factor_settings

        engine = FactorEngine(get_factor_settings())
        # 所有标的用同一组数据 → IC 完全相同
        df = make_ohlcv(320)

        async def _fake_same(**kwargs: Any) -> pd.DataFrame:
            return df

        engine._fetch_df = AsyncMock(side_effect=_fake_same)  # type: ignore[method-assign]

        result = await self._run(engine, ["BTC/USDT", "ETH/USDT"])
        ms = result["multi_symbol"]
        assert ms["n_symbols_evaluated"] == 2
        assert ms["cross_symbol_consistency"] is not None
        # 一致性接近 1.0（允许浮点误差）
        assert ms["cross_symbol_consistency"] > 0.99  # type: ignore[operator]

    @pytest.mark.asyncio
    async def test_multi_symbol_single(self, engine: Any) -> None:
        """symbols 只传 1 个标的，退化为单标的评估路径（不进入 _custom_score_multi）。"""
        result = await self._run(engine, ["BTC/USDT"])

        # 单标的应当走正常路径，没有 multi_symbol 字段
        assert result["available"] is True
        assert "multi_symbol" not in result


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