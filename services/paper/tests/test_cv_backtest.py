"""``run_cv_backtest`` 集成测试（ADR-0028 D2）—— 多路径分布 + DSR + WF 单路径。

策略复用内置 momentum_trend 骨架（main 上既有），经 dynamic_loader 加载，每个 split 由
工厂造新实例。
"""
from __future__ import annotations

import math

from inalpha_paper.engine.backtest import BacktestEngine, run_cv_backtest
from inalpha_paper.engine.cv import CombinatorialPurgedCV, WalkForward
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.strategy_authoring.archetypes import get_archetype
from inalpha_paper.strategy_authoring.dynamic_loader import load_strategy_class


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _bars(prices: list[float]) -> list[Bar]:
    step = 86_400 * 1_000_000_000  # 1d
    return [
        Bar(
            instrument_id=_btc(),
            timeframe="1d",
            open=p,
            high=p * 1.01,
            low=p * 0.99,
            close=p,
            volume=1.0 + (i % 5),
            ts_event=(i + 1) * step,
            ts_init=(i + 1) * step,
        )
        for i, p in enumerate(prices)
    ]


def _momentum_factory():
    cls = load_strategy_class(get_archetype("momentum_trend").code)

    def build(engine: BacktestEngine):
        return cls(
            name="cv-mom",
            clock=engine.clock,
            msgbus=engine.msgbus,
            instrument_id=_btc(),
            timeframe="1d",
            position_pct=1.0,
            initial_cash=10_000.0,
        )

    return build


def test_cpcv_backtest_produces_path_distribution() -> None:
    # 前半上行 + 后半下行，叠振荡 → 不同 fold 表现差异大，制造 Sharpe 分布
    prices = [
        100 + (0.5 * i if i < 150 else 0.5 * 150 - 0.5 * (i - 150))
        + 6 * math.sin(2 * math.pi * i / 15)
        for i in range(300)
    ]
    cv = CombinatorialPurgedCV(n_folds=6, n_test_folds=2)
    report = run_cv_backtest(
        build_strategy=_momentum_factory(), bars=_bars(prices), splitter=cv
    )
    assert report.n_paths == cv.n_paths() == 5
    assert len(report.sharpe_per_path) == 5
    assert len(report.max_dd_per_path) == 5
    assert report.n_splits == 30
    # 分位单调
    assert report.sharpe_p5 <= report.sharpe_p50 <= report.sharpe_p95
    # DSR 可算
    assert report.dsr is not None
    assert report.dsr_p_value is not None


def test_walkforward_backtest_single_path() -> None:
    prices = [100 + 0.3 * i + 5 * math.sin(2 * math.pi * i / 12) for i in range(200)]
    wf = WalkForward(test_size=20, train_size=40)
    report = run_cv_backtest(
        build_strategy=_momentum_factory(), bars=_bars(prices), splitter=wf
    )
    # WF 所有 test 段 path_id=0 → 拼成单条 OOS 路径
    assert report.n_paths == 1
    assert len(report.sharpe_per_path) == 1
    assert report.sharpe_p50 == report.sharpe_per_path[0]


def test_cv_report_max_dd_non_negative() -> None:
    prices = [100 + 0.4 * i + 7 * math.sin(2 * math.pi * i / 20) for i in range(300)]
    cv = CombinatorialPurgedCV(n_folds=5, n_test_folds=2)
    report = run_cv_backtest(
        build_strategy=_momentum_factory(), bars=_bars(prices), splitter=cv
    )
    assert all(dd >= 0.0 for dd in report.max_dd_per_path)
