"""D-8c · strategy compose 引擎单测。

覆盖：
- 4 个 family 路由正确（trend/mean_reversion/buy_hold/none）
- 参数 clip 到合法区间
- 字段名兼容（num_std → std_mult）
- factors 影响 trade_size
"""
from __future__ import annotations

from inalpha_paper.strategies.compose import (
    Factor,
    StrategyHint,
    compose_strategy,
)


# ────────────────────────────────────────────────────────────────────
# Trend → sma_cross
# ────────────────────────────────────────────────────────────────────


def test_trend_routes_to_sma_cross_with_clean_params() -> None:
    hint = StrategyHint(
        family="trend",
        params={"fast_period": 12, "slow_period": 36, "trade_size": 0.03},
        reasoning="momentum dominant",
    )
    factors = [
        Factor(name="sma_up", kind="momentum", value=1.0, strength=0.8),
    ]
    out = compose_strategy(hint, factors)

    assert out.strategy_id == "sma_cross"
    assert out.params == {"fast_period": 12, "slow_period": 36, "trade_size": 0.03}
    assert out.rejected_reason is None
    assert "sma_cross" in out.reasoning


def test_trend_clips_period_out_of_range() -> None:
    hint = StrategyHint(
        family="trend",
        params={"fast_period": 200, "slow_period": 500, "trade_size": 5.0},
    )
    out = compose_strategy(hint, [])
    assert out.strategy_id == "sma_cross"
    # clip 到 [5, 20] 和 [20, 60]
    assert out.params["fast_period"] == 20
    assert out.params["slow_period"] == 60
    # trade_size clip 到 0.05
    assert out.params["trade_size"] == 0.05


def test_trend_forces_slow_greater_than_fast() -> None:
    hint = StrategyHint(
        family="trend",
        params={"fast_period": 30, "slow_period": 20},
    )
    out = compose_strategy(hint, [])
    assert out.params["slow_period"] > out.params["fast_period"]


def test_trend_picks_defaults_when_params_missing() -> None:
    hint = StrategyHint(family="trend", params={})
    out = compose_strategy(hint, [])
    assert out.strategy_id == "sma_cross"
    assert 5 <= out.params["fast_period"] <= 20
    assert 20 <= out.params["slow_period"] <= 60
    assert out.params["slow_period"] > out.params["fast_period"]


def test_trade_size_scales_with_factor_strength() -> None:
    weak = compose_strategy(
        StrategyHint(family="trend", params={}),
        [Factor(name="x", kind="momentum", value=0, strength=0.1)],
    )
    strong = compose_strategy(
        StrategyHint(family="trend", params={}),
        [Factor(name="x", kind="momentum", value=0, strength=0.95)],
    )
    assert strong.params["trade_size"] > weak.params["trade_size"]


# ────────────────────────────────────────────────────────────────────
# Mean reversion → mean_reversion
# ────────────────────────────────────────────────────────────────────


def test_mean_reversion_routes_correctly() -> None:
    hint = StrategyHint(
        family="mean_reversion",
        params={"period": 15, "std_mult": 2.0, "trade_size": 0.02},
    )
    out = compose_strategy(hint, [])
    assert out.strategy_id == "mean_reversion"
    assert out.params == {"period": 15, "std_mult": 2.0, "trade_size": 0.02}


def test_mean_reversion_accepts_num_std_alias() -> None:
    """LLM 可能用 num_std；compose 应该映射成 std_mult。"""
    hint = StrategyHint(
        family="mean_reversion",
        params={"period": 20, "num_std": 1.8},
    )
    out = compose_strategy(hint, [])
    assert "std_mult" in out.params
    assert out.params["std_mult"] == 1.8
    assert "num_std" not in out.params


def test_mean_reversion_clips_std_mult() -> None:
    hint = StrategyHint(
        family="mean_reversion",
        params={"std_mult": 10.0},
    )
    out = compose_strategy(hint, [])
    assert out.params["std_mult"] == 2.5  # upper bound


# ────────────────────────────────────────────────────────────────────
# Buy hold
# ────────────────────────────────────────────────────────────────────


def test_buy_hold_routes_correctly() -> None:
    hint = StrategyHint(
        family="buy_hold",
        params={"trade_size": 0.7},
    )
    out = compose_strategy(hint, [])
    assert out.strategy_id == "buy_and_hold"
    assert out.params == {"trade_size": 0.7}


def test_buy_hold_defaults() -> None:
    hint = StrategyHint(family="buy_hold", params={})
    out = compose_strategy(hint, [])
    assert out.strategy_id == "buy_and_hold"
    assert out.params["trade_size"] == 0.5


# ────────────────────────────────────────────────────────────────────
# None / reject
# ────────────────────────────────────────────────────────────────────


def test_none_family_rejected() -> None:
    hint = StrategyHint(family="none", reasoning="ambiguous")
    out = compose_strategy(hint, [])
    assert out.strategy_id is None
    assert out.rejected_reason is not None
    assert "none" in out.rejected_reason.lower()


# ────────────────────────────────────────────────────────────────────
# Reasoning 串字段
# ────────────────────────────────────────────────────────────────────


def test_reasoning_includes_hint_and_factor_names() -> None:
    hint = StrategyHint(
        family="trend",
        params={},
        reasoning="strong upward momentum",
    )
    factors = [
        Factor(name="sma20_cross_up", kind="momentum", value=1.0, strength=0.7),
        Factor(name="rsi_neutral", kind="mean_reversion", value=58, strength=0.3),
    ]
    out = compose_strategy(hint, factors)
    assert "strong upward momentum" in out.reasoning
    assert "sma20_cross_up" in out.reasoning


# ────────────────────────────────────────────────────────────────────
# 防御性 coercion（LLM 字段类型不规范）
# ────────────────────────────────────────────────────────────────────


def test_coerces_string_numeric_params() -> None:
    """LLM 偶尔会把数字给成 string '12'，compose 应该能转。"""
    hint = StrategyHint(
        family="trend",
        params={"fast_period": "12", "slow_period": "36"},
    )
    out = compose_strategy(hint, [])
    assert out.params["fast_period"] == 12
    assert out.params["slow_period"] == 36


def test_handles_garbage_param_with_default() -> None:
    """LLM 给的 trade_size 是字符串 'small' 这种 → 默认值兜底。"""
    hint = StrategyHint(
        family="trend",
        params={"trade_size": "small"},
    )
    out = compose_strategy(hint, [])
    assert 0.01 <= out.params["trade_size"] <= 0.05


# ────────────────────────────────────────────────────────────────────
# 输出参数能被对应 Strategy 构造器消费（结构验证 —— 不实例化引擎）
# ────────────────────────────────────────────────────────────────────


def test_sma_cross_params_match_constructor_signature() -> None:
    """compose 输出的 params 必须只含 SMACrossStrategy.__init__ 可接受的字段。"""
    out = compose_strategy(StrategyHint(family="trend", params={}), [])
    assert set(out.params.keys()) == {"fast_period", "slow_period", "trade_size"}


def test_mean_reversion_params_match_constructor_signature() -> None:
    out = compose_strategy(StrategyHint(family="mean_reversion", params={}), [])
    assert set(out.params.keys()) == {"period", "std_mult", "trade_size"}


def test_buy_hold_params_match_constructor_signature() -> None:
    out = compose_strategy(StrategyHint(family="buy_hold", params={}), [])
    assert set(out.params.keys()) == {"trade_size"}
