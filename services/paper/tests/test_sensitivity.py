"""参数敏感性检查单测（D-12）：build_neighborhood / summarize_neighbors 纯函数 + 端点。"""
from __future__ import annotations

import respx
from fastapi.testclient import TestClient
from httpx import Response

from inalpha_paper.schemas import SensitivityNeighbor
from inalpha_paper.sensitivity import build_neighborhood, summarize_neighbors

from .test_api_backtest import _bars_for_oscillating

# ────────────────────────────────────────────────────────────────────
# build_neighborhood
# ────────────────────────────────────────────────────────────────────


def test_one_at_a_time_two_per_numeric_param() -> None:
    combos = build_neighborhood({"fast_period": 10, "slow_period": 30})
    assert len(combos) == 4
    # ±20%：fast 8/12，slow 24/36；每个组合只动一个参数
    assert {c["fast_period"] for c in combos} == {8, 10, 12}
    assert {c["slow_period"] for c in combos} == {24, 30, 36}
    for c in combos:
        assert (c["fast_period"] == 10) != (c["slow_period"] == 30)


def test_small_int_degenerates_to_plus_minus_one() -> None:
    """±20% 取整等于原值的小整数 → 退化为 ±1，仍有邻域。"""
    combos = build_neighborhood({"period": 3})
    assert {c["period"] for c in combos} == {2, 4}


def test_sizing_and_non_numeric_params_skipped() -> None:
    combos = build_neighborhood(
        {"fast_period": 10, "trade_size": 0.02, "position_pct": 1.0, "mode": "x", "flag": True}
    )
    # 只有 fast_period 被扰动
    assert len(combos) == 2
    for c in combos:
        assert c["trade_size"] == 0.02
        assert c["position_pct"] == 1.0
        assert c["mode"] == "x"
        assert c["flag"] is True


def test_max_combos_cap() -> None:
    params = {f"p{i}": 10 + i for i in range(20)}  # 20 个参数 → 理论 40 组
    combos = build_neighborhood(params, max_combos=16)
    assert len(combos) == 16


def test_float_param_perturbed() -> None:
    combos = build_neighborhood({"num_std": 2.0})
    assert {c["num_std"] for c in combos} == {1.6, 2.4}


# ────────────────────────────────────────────────────────────────────
# summarize_neighbors
# ────────────────────────────────────────────────────────────────────


def _neighbors(fitnesses: list[float | None]) -> list[SensitivityNeighbor]:
    return [
        SensitivityNeighbor(params={"i": i}, fitness=f, error=None if f is not None else "err")
        for i, f in enumerate(fitnesses)
    ]


def test_verdict_robust_when_plateau() -> None:
    stats, verdict = summarize_neighbors(1.0, _neighbors([0.9, 0.8, 1.1, 0.7]))
    assert verdict == "robust"
    assert stats.worst == 0.7
    assert stats.n_ok == 4


def test_verdict_cliff_when_worst_below_half_base() -> None:
    stats, verdict = summarize_neighbors(1.0, _neighbors([0.9, 0.2, 1.1, 0.8]))
    assert verdict == "cliff"
    assert stats.worst == 0.2


def test_verdict_insufficient_when_too_few_ok() -> None:
    _stats, verdict = summarize_neighbors(1.0, _neighbors([0.9, None, None, 0.8]))
    assert verdict == "insufficient"


def test_verdict_insufficient_when_base_nonpositive() -> None:
    """base fitness ≤ 0：策略本身不及格，谈稳健性无意义。"""
    _stats, verdict = summarize_neighbors(-0.5, _neighbors([0.1, 0.2, 0.3, 0.4]))
    assert verdict == "insufficient"


def test_failed_neighbors_counted() -> None:
    stats, _ = summarize_neighbors(1.0, _neighbors([0.9, None, 0.8, 0.7, 0.6]))
    assert stats.n_failed == 1
    assert stats.n_ok == 4


# ────────────────────────────────────────────────────────────────────
# 端点（mocked data-service，内置策略路径免 DB）
# ────────────────────────────────────────────────────────────────────


@respx.mock
def test_sensitivity_endpoint_e2e(client: TestClient, auth_headers: dict[str, str]) -> None:
    """sma_cross 振荡市：base + 4 邻域跑通，verdict 三值之一，邻域不落 run。"""
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars_for_oscillating(120))
    )
    r = client.post(
        "/backtest/sensitivity",
        headers=auth_headers,
        json={
            "strategy_id": "sma_cross",
            "params": {"fast_period": 5, "slow_period": 15, "trade_size": 0.05},
            "venue": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-06T00:00:00Z",
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["verdict"] in ("robust", "cliff", "insufficient")
    assert len(body["neighbors"]) == 4
    assert body["stats"]["n_ok"] + body["stats"]["n_failed"] == 4
    assert isinstance(body["base_fitness"], float)


@respx.mock
def test_sensitivity_invalid_combo_recorded_not_raised(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """fast=13(+20%→16) > slow=15(-20%→12) 的扰动组合会被策略构造拒绝 → 记 error 不 500。"""
    respx.get("http://data-mock.test/bars").mock(
        return_value=Response(200, json=_bars_for_oscillating(120))
    )
    r = client.post(
        "/backtest/sensitivity",
        headers=auth_headers,
        json={
            "strategy_id": "sma_cross",
            "params": {"fast_period": 13, "slow_period": 15, "trade_size": 0.05},
            "venue": "binance",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-06T00:00:00Z",
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    # fast→16 与 slow→12 两个组合非法（fast >= slow）
    failed = [n for n in body["neighbors"] if n["fitness"] is None]
    assert len(failed) >= 1
    assert body["stats"]["n_failed"] >= 1


def test_sensitivity_requires_exactly_one_strategy_source(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    r = client.post(
        "/backtest/sensitivity",
        headers=auth_headers,
        json={
            "params": {"fast_period": 5},
            "symbol": "BTC/USDT",
            "from_ts": "2026-01-01T00:00:00Z",
            "to_ts": "2026-01-06T00:00:00Z",
        },
    )
    # 共享错误处理把 body 校验错误统一映射 400（inalpha_shared.errors）
    assert r.status_code == 400
