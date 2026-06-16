"""_validation_from_report 纯函数单测（D-12 · holdout 时间切分验证）。

不跑引擎：用合成 equity_curve / fills 直接喂，验证切段口径、衰减比与 flags。
"""
from __future__ import annotations

from types import SimpleNamespace

from inalpha_paper.runner import _validation_from_report

_HOUR_NS = 3_600 * 1_000_000_000


def _report(
    equities: list[float],
    *,
    fill_idxs: list[int] | None = None,
) -> SimpleNamespace:
    """合成最小 report：等距 1h 曲线 + 指定 bar 序号上的 fills。"""
    curve = [(i * _HOUR_NS, eq) for i, eq in enumerate(equities)]
    fills = [SimpleNamespace(ts_ns=i * _HOUR_NS) for i in (fill_idxs or [])]
    return SimpleNamespace(equity_curve=curve, fills=fills, num_trades=len(fills))


def test_returns_none_when_curve_too_short() -> None:
    assert (
        _validation_from_report(_report([100.0] * 5), split=0.7, bars_per_year=8760)
        is None
    )


def test_segments_split_by_bar_count_and_fills_by_ts() -> None:
    """100 根曲线 0.7 切分：train 70 根、holdout 31 根（带切点前一根作基准）。"""
    equities = [100.0 + i for i in range(100)]
    report = _report(equities, fill_idxs=[5, 30, 69, 70, 90])
    block = _validation_from_report(report, split=0.7, bars_per_year=8760)

    assert block is not None
    assert block.split_ratio == 0.7
    assert block.train.num_bars == 70
    assert block.holdout.num_bars == 31
    # fills 按切点 ts 分段：idx 5/30/69 < 70 → train 3 笔；70/90 → holdout 2 笔
    assert block.train.num_trades == 3
    assert block.holdout.num_trades == 2
    assert block.train.total_return_pct > 0
    assert block.holdout.total_return_pct > 0


def test_decay_ratio_flags_overfit_shape() -> None:
    """train 段稳涨、holdout 段稳跌 → decay_ratio 为负（典型过拟合形状）。"""
    # train：100 → 170 带轻微噪声（保证 std > 0）；holdout：170 → 140
    train = [100.0 + i + (0.3 if i % 2 else -0.3) for i in range(70)]
    holdout = [train[-1] - i - (0.2 if i % 2 else -0.2) for i in range(1, 31)]
    report = _report(train + holdout, fill_idxs=[1, 2, 3, 4, 5, 80])
    block = _validation_from_report(report, split=0.7, bars_per_year=8760)

    assert block is not None
    assert block.train.sharpe is not None and block.train.sharpe > 0
    assert block.holdout.sharpe is not None and block.holdout.sharpe < 0
    assert block.decay_ratio is not None and block.decay_ratio < 0


def test_train_sharpe_nonpositive_yields_null_decay_with_flag() -> None:
    """train 段本身亏：负除负会假装'没衰减'，必须 null + flag。"""
    train = [100.0 - i + (0.3 if i % 2 else -0.3) for i in range(70)]
    holdout = [train[-1] - i for i in range(1, 31)]
    report = _report(train + holdout, fill_idxs=[1, 2, 3, 4, 5, 80])
    block = _validation_from_report(report, split=0.7, bars_per_year=8760)

    assert block is not None
    assert block.decay_ratio is None
    assert "train_sharpe_nonpositive" in block.flags


def test_insufficient_sample_flagged() -> None:
    """holdout < 30 根或全程 trades < 5 → insufficient_sample。"""
    equities = [100.0 + i * 0.5 + (0.2 if i % 2 else -0.2) for i in range(60)]
    report = _report(equities, fill_idxs=[10, 50])  # 2 笔 < 5
    block = _validation_from_report(report, split=0.7, bars_per_year=8760)

    assert block is not None
    assert "insufficient_sample" in block.flags


def test_insufficient_sample_when_holdout_thin_but_total_ok() -> None:
    """全窗口交易够（≥5）但 holdout 段 <2 笔 → 仍 flag（CR #86：holdout 段自身判据）。"""
    # 100 根曲线 0.7 切：train 70 / holdout 31。fills 大多在 train，holdout 仅 1 笔
    equities = [100.0 + i * 0.3 + (0.2 if i % 2 else -0.2) for i in range(100)]
    report = _report(equities, fill_idxs=[5, 10, 20, 40, 60, 95])  # 5 笔 train + 1 笔 holdout
    block = _validation_from_report(report, split=0.7, bars_per_year=8760)

    assert block is not None
    assert block.train.num_trades >= 5  # 全窗口看着够
    assert block.holdout.num_trades < 2  # 但 holdout 段几乎没交易
    assert "insufficient_sample" in block.flags  # 必须 flag


def test_flat_segment_sharpe_undefined_flag() -> None:
    """holdout 完全平稳（零波动）→ sharpe null → sharpe_undefined flag。"""
    train = [100.0 + i + (0.3 if i % 2 else -0.3) for i in range(70)]
    holdout = [train[-1]] * 30
    report = _report(train + holdout, fill_idxs=[1, 2, 3, 4, 5, 80])
    block = _validation_from_report(report, split=0.7, bars_per_year=8760)

    assert block is not None
    assert block.holdout.sharpe is None
    assert block.decay_ratio is None
    assert "sharpe_undefined" in block.flags


def test_bootstrap_ci_present_when_enough_holdout_returns() -> None:
    """holdout returns ≥ 30 时带 bootstrap CI 判定。"""
    train = [100.0 + i + (0.5 if i % 2 else -0.5) for i in range(100)]
    holdout = [train[-1] + i * 0.01 + (0.5 if i % 2 else -0.5) for i in range(1, 51)]
    report = _report(train + holdout, fill_idxs=[1, 2, 3, 4, 5, 120])
    block = _validation_from_report(report, split=100 / 150, bars_per_year=8760)

    assert block is not None
    assert block.holdout_sharpe_ci_includes_zero is not None
