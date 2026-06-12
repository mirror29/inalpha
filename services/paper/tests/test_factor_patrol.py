"""因子衰减巡检测试（D-12 · ADR-0047）。

覆盖告警状态机与降级边界：

- 基准拍摄：lineage（候选声明了 factor_snapshot）走 /score；只拍一次不覆盖
- 巡检告警：进入 decaying 告警一次；持续 decaying 不重复；恢复后 info + 重置
- factor 服务不可用：capture / patrol 都静默跳过，不抛、不动 run
"""
from __future__ import annotations

from typing import Any, ClassVar
from uuid import uuid4

import pytest
from inalpha_shared.db import get_conn

import inalpha_paper.factor_patrol as patrol_mod
from inalpha_paper.config import get_paper_settings
from inalpha_paper.factor_client import FactorServiceError
from inalpha_paper.factor_patrol import FactorPatrol, capture_factor_baseline
from inalpha_paper.storage import strategy_candidates as candidates_store
from inalpha_paper.storage import strategy_runs as runs_store

pytestmark = pytest.mark.integration


_LINEAGE = {
    "venue": "binance",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "factors": [{"id": "ta.rsi_14", "rank_ic": 0.08, "decay_state": "stable"}],
    "source": "author_tool",
}


def _eff(factor_id: str, *, decay_state: str, rank_ic: float = 0.08) -> dict[str, Any]:
    """构造 /score 响应里的一行 FactorEffectiveness（只含巡检用到的字段）。"""
    return {
        "factor_id": factor_id,
        "rank_ic": rank_ic,
        "rank_ic_recent": rank_ic if decay_state == "stable" else -0.01,
        "direction": 1,
        "decay_state": decay_state,
        "low_confidence": False,
    }


def _score_resp(*factors: dict[str, Any]) -> dict[str, Any]:
    return {
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "as_of": "2026-06-11T00:00:00Z",
        "horizon_bars": 5,
        "bars_used": 720,
        "factors": list(factors),
    }


class _StubFactorClient:
    """monkeypatch 替身：类属性注入响应 / 故障。"""

    score_response: ClassVar[dict[str, Any]] = {}
    raise_error: ClassVar[bool] = False
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _StubFactorClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    async def close(self) -> None:
        pass

    async def score(self, **kwargs: Any) -> dict[str, Any]:
        if type(self).raise_error:
            raise FactorServiceError("factor-service down (stub)")
        type(self).calls.append({"endpoint": "score", **kwargs})
        return type(self).score_response

    async def snapshot(self, **kwargs: Any) -> dict[str, Any]:
        if type(self).raise_error:
            raise FactorServiceError("factor-service down (stub)")
        type(self).calls.append({"endpoint": "snapshot", **kwargs})
        return {"top_factors": [], "as_of": "2026-06-11T00:00:00Z"}


@pytest.fixture(autouse=True)
def _stub_factor_client(monkeypatch: pytest.MonkeyPatch) -> type[_StubFactorClient]:
    _StubFactorClient.score_response = _score_resp(_eff("ta.rsi_14", decay_state="stable"))
    _StubFactorClient.raise_error = False
    _StubFactorClient.calls = []
    monkeypatch.setattr(patrol_mod, "FactorClient", _StubFactorClient)
    return _StubFactorClient


async def _insert_run_with_lineage() -> dict[str, Any]:
    """候选（带 lineage）+ running run。"""
    async with get_conn() as conn:
        candidate_id, _ = await candidates_store.insert_candidate(
            conn,
            code=f'"factor-patrol test candidate {uuid4().hex}"\n',
            factor_snapshot=_LINEAGE,
        )
        return await runs_store.insert(
            conn, candidate_id=candidate_id, account_id=uuid4(),
            venue="binance", symbol="BTC/USDT", timeframe="1h", params={},
        )


def _log_codes(run: dict[str, Any]) -> list[str | None]:
    return [entry.get("code") for entry in (run.get("run_log") or [])]


async def test_capture_baseline_lineage_via_score(app_with_lifespan: Any) -> None:
    """有 lineage 的 run：基准走 /score（带声明的 factor_ids），source=lineage。"""
    run = await _insert_run_with_lineage()
    await capture_factor_baseline(run, get_paper_settings())

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    baseline = fresh["factor_baseline"]
    assert baseline["source"] == "lineage"
    assert [f["id"] for f in baseline["factors"]] == ["ta.rsi_14"]
    assert _StubFactorClient.calls[0]["endpoint"] == "score"
    assert _StubFactorClient.calls[0]["factor_ids"] == ["ta.rsi_14"]


async def test_capture_baseline_only_once(app_with_lifespan: Any) -> None:
    """基准只拍一次：二拍（不同读数）不覆盖入场锚点。"""
    run = await _insert_run_with_lineage()
    await capture_factor_baseline(run, get_paper_settings())

    _StubFactorClient.score_response = _score_resp(
        _eff("ta.rsi_14", decay_state="decaying", rank_ic=0.01)
    )
    await capture_factor_baseline(run, get_paper_settings())

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert fresh["factor_baseline"]["factors"][0]["rank_ic"] == 0.08  # 仍是首拍值


async def test_capture_baseline_service_down_is_silent(app_with_lifespan: Any) -> None:
    """factor 服务不可用：不抛、baseline 保持 NULL（留给巡检自愈）。"""
    run = await _insert_run_with_lineage()
    _StubFactorClient.raise_error = True
    await capture_factor_baseline(run, get_paper_settings())  # 不应 raise

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None and fresh["factor_baseline"] is None


async def test_patrol_alerts_once_then_recovers(app_with_lifespan: Any) -> None:
    """状态机全周期：decaying 告警一次 → 持续不重复 → 恢复 info + 重置。"""
    run = await _insert_run_with_lineage()
    await capture_factor_baseline(run, get_paper_settings())
    patrol = FactorPatrol(settings=get_paper_settings())

    # 第一轮：进入 decaying → 一条 warn(factor_decay) + 状态机记 decaying
    _StubFactorClient.score_response = _score_resp(
        _eff("ta.rsi_14", decay_state="decaying", rank_ic=0.02)
    )
    await patrol.patrol_once()
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert _log_codes(fresh).count("factor_decay") == 1
    assert fresh["factor_alerts"]["ta.rsi_14"]["state"] == "decaying"
    assert "ta.rsi_14" in str(fresh["run_log"])

    # 第二轮：仍 decaying → 不重复告警
    await patrol.patrol_once()
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert _log_codes(fresh).count("factor_decay") == 1

    # 第三轮：恢复 stable → info(factor_decay_recovered) + 状态机重置
    _StubFactorClient.score_response = _score_resp(_eff("ta.rsi_14", decay_state="stable"))
    await patrol.patrol_once()
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert _log_codes(fresh).count("factor_decay_recovered") == 1
    assert fresh["factor_alerts"]["ta.rsi_14"]["state"] == "stable"

    # 第四轮：再次 decaying → 重新告警（重置后允许新一轮）
    _StubFactorClient.score_response = _score_resp(
        _eff("ta.rsi_14", decay_state="decaying", rank_ic=0.02)
    )
    await patrol.patrol_once()
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert _log_codes(fresh).count("factor_decay") == 2


async def test_patrol_alerts_when_rank_ic_is_none(app_with_lifespan: Any) -> None:
    """decaying 但服务端返回 rank_ic=None：告警文案不能因 None:.4f 崩，必须照常 warn。"""
    run = await _insert_run_with_lineage()
    await capture_factor_baseline(run, get_paper_settings())
    patrol = FactorPatrol(settings=get_paper_settings())

    decaying_no_ic = _eff("ta.rsi_14", decay_state="decaying")
    decaying_no_ic["rank_ic"] = None
    decaying_no_ic["rank_ic_recent"] = None
    _StubFactorClient.score_response = _score_resp(decaying_no_ic)

    await patrol.patrol_once()
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    # 告警照常触发（不再死循环静默吞），文案里 None 字段降级成 n/a
    assert _log_codes(fresh).count("factor_decay") == 1
    assert "n/a" in str(fresh["run_log"])


async def test_patrol_skips_when_decay_state_absent(app_with_lifespan: Any) -> None:
    """factor 服务版本还没 decay_state 字段（滚动升级）：本轮跳过，不误报告警。"""
    run = await _insert_run_with_lineage()
    await capture_factor_baseline(run, get_paper_settings())
    patrol = FactorPatrol(settings=get_paper_settings())

    no_state = _eff("ta.rsi_14", decay_state="stable")
    del no_state["decay_state"]  # 模拟旧版 factor service 响应缺字段
    _StubFactorClient.score_response = _score_resp(no_state)

    await patrol.patrol_once()
    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert _log_codes(fresh).count("factor_decay") == 0  # 缺字段 → 不误报
    assert "ta.rsi_14" not in (fresh["factor_alerts"] or {})


async def test_patrol_self_heals_missing_baseline(app_with_lifespan: Any) -> None:
    """起跑时没拍到基准的 run：巡检首轮补拍（本轮不对比）。"""
    run = await _insert_run_with_lineage()  # 不调 capture
    patrol = FactorPatrol(settings=get_paper_settings())
    await patrol.patrol_once()

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert fresh["factor_baseline"] is not None
    assert _log_codes(fresh).count("factor_decay") == 0  # 补拍轮不告警


async def test_patrol_service_down_keeps_runs_untouched(app_with_lifespan: Any) -> None:
    """巡检中 factor 服务不可用：不抛、不写告警、不动状态机。"""
    run = await _insert_run_with_lineage()
    await capture_factor_baseline(run, get_paper_settings())
    _StubFactorClient.raise_error = True

    patrol = FactorPatrol(settings=get_paper_settings())
    await patrol.patrol_once()  # 不应 raise

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert _log_codes(fresh).count("factor_decay") == 0
    assert fresh["factor_alerts"] == {}


async def test_patrol_skips_low_confidence(app_with_lifespan: Any) -> None:
    """低置信因子的 decay_state 是噪声：不驱动状态机。"""
    run = await _insert_run_with_lineage()
    await capture_factor_baseline(run, get_paper_settings())
    low_conf = _eff("ta.rsi_14", decay_state="decaying", rank_ic=0.02)
    low_conf["low_confidence"] = True
    _StubFactorClient.score_response = _score_resp(low_conf)

    patrol = FactorPatrol(settings=get_paper_settings())
    await patrol.patrol_once()

    async with get_conn() as conn:
        fresh = await runs_store.get(conn, run["id"])
    assert fresh is not None
    assert _log_codes(fresh).count("factor_decay") == 0
