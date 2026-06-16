"""策略原型库测试（ADR-0051 D1）。

红线：每个原型的 ``code`` 必须
1. 过 ``ast_audit``（沙盒第 1 道关）
2. 能 ``load_strategy_class`` + ``verify_strategy_contract``（第 2 道关）
3. 能被 ``BacktestEngine`` 实例化 + 跑完一段合成行情（end-to-end，证明 on_bar 真响应）

骨架坏 = CI 红，防止"骨架给 agent 当起点，结果第一步就 422"。
"""
from __future__ import annotations

import math

import pytest

from inalpha_paper.engine.backtest import BacktestEngine
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.data import Bar
from inalpha_paper.strategy_authoring.archetypes import (
    ARCHETYPES,
    get_archetype,
    list_archetypes,
)
from inalpha_paper.strategy_authoring.ast_audit import audit_strategy_code
from inalpha_paper.strategy_authoring.contract_check import verify_strategy_contract
from inalpha_paper.strategy_authoring.dynamic_loader import load_strategy_class

_ALL_NAMES = [a.name for a in ARCHETYPES]


def _btc() -> InstrumentId:
    return InstrumentId(symbol="BTC/USDT", venue="binance")


def _gen_bars(prices: list[float]) -> list[Bar]:
    step_ns = 3600 * 1_000_000_000
    return [
        Bar(
            instrument_id=_btc(),
            timeframe="1h",
            open=p,
            high=p,
            low=p,
            close=p,
            volume=1.0 + (i % 5),  # 量能有起伏，触发量能/量因子分支
            ts_event=(i + 1) * step_ns,
            ts_init=(i + 1) * step_ns,
        )
        for i, p in enumerate(prices)
    ]


@pytest.mark.parametrize("name", _ALL_NAMES)
def test_archetype_passes_ast_audit(name: str) -> None:
    meta = get_archetype(name)
    assert meta is not None
    result = audit_strategy_code(meta.code)
    assert result.ok, f"{name} 未过 ast_audit:\n{result.reason()}"


@pytest.mark.parametrize("name", _ALL_NAMES)
def test_archetype_loads_and_satisfies_contract(name: str) -> None:
    meta = get_archetype(name)
    assert meta is not None
    cls = load_strategy_class(meta.code)
    # 不抛即通过协议契约（覆写 on_bar + __init__ 五个必传 kw）
    verify_strategy_contract(cls)


@pytest.mark.parametrize("name", _ALL_NAMES)
def test_archetype_runs_end_to_end(name: str) -> None:
    """合成振荡 + 趋势混合行情，跑完整回测，证明 on_bar 不崩、能产出报告。"""
    meta = get_archetype(name)
    assert meta is not None
    cls = load_strategy_class(meta.code)

    # 振荡叠加缓升：让趋势 / 回归 / 突破 / 多因子各自的分支都有机会触发
    prices = [
        100 + 0.1 * i + 8 * math.sin(2 * math.pi * i / 20) for i in range(120)
    ]
    bars = _gen_bars(prices)

    engine = BacktestEngine(initial_cash=10_000.0, fee_rate=0.001)
    strat = cls(
        name=f"{name}-test",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=_btc(),
        timeframe="1h",
        position_pct=1.0,
        initial_cash=10_000.0,
    )
    engine.add_strategy(strat)
    report = engine.run(bars)

    # 跑完不崩、报告字段就位即算通过（不对收益做断言——骨架不保证 alpha）
    assert report.num_bars_processed == len(bars)
    assert report.final_equity > 0
    assert not report.blew_up, f"{name} 物理穿仓，骨架有 bug"


def test_list_archetypes_default_returns_all() -> None:
    metas = list_archetypes()
    assert [m.name for m in metas] == _ALL_NAMES


def test_list_archetypes_ranks_by_factor_kind() -> None:
    # mean_reversion kind → mean_reversion 骨架应排第一
    metas = list_archetypes(["mean_reversion"])
    assert metas[0].name == "mean_reversion"
    # 全部仍在（只排序不过滤）
    assert {m.name for m in metas} == set(_ALL_NAMES)


def test_list_archetypes_unknown_kind_keeps_order() -> None:
    metas = list_archetypes(["does_not_exist"])
    assert [m.name for m in metas] == _ALL_NAMES


def test_archetype_meta_fields_present() -> None:
    for a in ARCHETYPES:
        assert a.name and a.description and a.when_to_use and a.when_not_to_use
        assert a.applies_to_kinds
        assert a.params  # 每个骨架至少 1 个可调参数槽
        assert a.code.startswith("class ")
