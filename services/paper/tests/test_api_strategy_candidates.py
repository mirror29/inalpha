"""``POST /strategy_candidates/{id}/promote`` 端点测试。

覆盖：

- 正常 promote：fitness 非空 → 200 + status 切换为 ``promoted`` + ``audit.promotion`` 记 metadata
- 重复 promote：第二次 → 409 ``CANDIDATE_NOT_PROMOTABLE``
- 没回测就 promote：fitness=None → 400 ``CANDIDATE_NOT_BACKTESTED``
- 不存在的 candidate_id → 404 ``CANDIDATE_NOT_FOUND``

依赖 conftest 的 ``client`` + ``auth_headers`` fixture（已起 lifespan + DB pool）。
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.storage import strategy_candidates as candidates_store

pytestmark = pytest.mark.integration


_MIN_STRATEGY = """
class PromoteMeStrategy(Strategy):
    def __init__(self, name, clock, msgbus, instrument_id, timeframe="1h", trade_size=0.01):
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe
        self._trade_size = trade_size

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        pass
"""


@pytest_asyncio.fixture
async def candidate_id(client: TestClient, auth_headers: dict[str, str]) -> str:
    """落一个 fresh candidate（每次结构可区分 salt 让候选唯一），返回 ID。"""
    # salt 作 STRING 字面量（非注释）：结构指纹去重剥注释后再 hash，注释 salt 会让所有
    # candidate_id fixture 结构相同 → dedup 成同一个 → 跨 test 串扰（如 promote_twice 误命中已 promoted）。
    # 裸 STRING 表达式 AST 审计安全（同 docstring），且被结构指纹保留 → 每个候选唯一。
    salt = uuid.uuid4().hex[:8]
    code = _MIN_STRATEGY + f'\n"structural salt {salt}"\n'
    r = client.post(
        "/strategy_candidates",
        headers=auth_headers,
        json={"code": code, "description": f"promote-test-{salt}"},
    )
    assert r.status_code == 200, r.json()
    return r.json()["candidate_id"]


async def _set_fitness(candidate_id_str: str, fitness: float) -> None:
    """绕过 backtest 链路直接给 candidate 回填 fitness（隔离 promote 端点测试）。"""
    async with get_conn() as conn:
        await candidates_store.update_after_backtest(
            conn,
            uuid.UUID(candidate_id_str),
            metrics={"sharpe": 1.5, "max_drawdown_pct": 8.0},
            fitness=fitness,
            backtest_run_id=None,
        )


@pytest.mark.asyncio
async def test_promote_returns_404_when_candidate_missing(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    missing = uuid.uuid4()
    r = client.post(
        f"/strategy_candidates/{missing}/promote",
        headers=auth_headers,
        json={"reason": "测试 404 路径，候选不存在"},
    )
    assert r.status_code == 404, r.json()
    body = r.json()
    assert body["code"] == "CANDIDATE_NOT_FOUND"


@pytest.mark.asyncio
async def test_promote_returns_422_when_fitness_missing(
    client: TestClient,
    auth_headers: dict[str, str],
    candidate_id: str,
) -> None:
    # fresh candidate 默认 fitness=None
    r = client.post(
        f"/strategy_candidates/{candidate_id}/promote",
        headers=auth_headers,
        json={
            "reason": "试图在没跑回测时 promote，应该被挡住",
        },
    )
    # ValidationError 默认 400（跟 STRATEGY_AUDIT_FAILED 一致）
    assert r.status_code == 400, r.json()
    body = r.json()
    assert body["code"] == "CANDIDATE_NOT_BACKTESTED"


@pytest.mark.asyncio
async def test_promote_happy_path(
    client: TestClient,
    auth_headers: dict[str, str],
    candidate_id: str,
) -> None:
    await _set_fitness(candidate_id, fitness=0.85)

    r = client.post(
        f"/strategy_candidates/{candidate_id}/promote",
        headers=auth_headers,
        json={
            "reason": "2026-Q2 BTC 1h fitness=0.85 vs baseline=0.32，calmar≈4，下行可控",
        },
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["id"] == candidate_id
    assert body["status"] == "promoted"
    assert body["audit"] is not None
    promotion = body["audit"].get("promotion")
    assert promotion is not None, body["audit"]
    assert "2026-Q2" in promotion["reason"]
    assert promotion["promoted_by"]
    assert promotion["promoted_at"].endswith("Z")

    # GET 拉一遍确认落库
    r2 = client.get(
        f"/strategy_candidates/{candidate_id}",
        headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "promoted"


@pytest.mark.asyncio
async def test_promote_twice_returns_409(
    client: TestClient,
    auth_headers: dict[str, str],
    candidate_id: str,
) -> None:
    await _set_fitness(candidate_id, fitness=0.9)
    payload = {"reason": "第一次 promote 正常路径"}
    r1 = client.post(
        f"/strategy_candidates/{candidate_id}/promote",
        headers=auth_headers,
        json=payload,
    )
    assert r1.status_code == 200, r1.json()

    r2 = client.post(
        f"/strategy_candidates/{candidate_id}/promote",
        headers=auth_headers,
        json={"reason": "重复 promote，应该被状态机挡住"},
    )
    assert r2.status_code == 409, r2.json()
    body = r2.json()
    assert body["code"] == "CANDIDATE_NOT_PROMOTABLE"
    assert body["details"]["current_status"] == "promoted"


# ────────────────────────────────────────────────────────────────────
# 因子血缘 factor_snapshot（ADR-0047）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_factor_snapshot_round_trip(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """POST 带 factor_snapshot → GET 原样返回（血缘落库）。"""
    salt = uuid.uuid4().hex[:8]
    code = _MIN_STRATEGY + f'\n"lineage salt {salt}"\n'
    snapshot = {
        "venue": "binance",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "as_of": "2026-06-11T00:00:00Z",
        "factors": [
            {
                "id": "ta.rsi_14",
                "rank_ic": 0.08,
                "rank_ic_recent": 0.06,
                "direction": 1,
                "decay_state": "stable",
            }
        ],
        "source": "author_tool",
    }
    r = client.post(
        "/strategy_candidates",
        headers=auth_headers,
        json={"code": code, "description": f"lineage-{salt}", "factor_snapshot": snapshot},
    )
    assert r.status_code == 200, r.json()
    cid = r.json()["candidate_id"]

    got = client.get(f"/strategy_candidates/{cid}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["factor_snapshot"] == snapshot


@pytest.mark.asyncio
async def test_factor_snapshot_absent_stays_null(
    client: TestClient,
    auth_headers: dict[str, str],
    candidate_id: str,
) -> None:
    """不传 factor_snapshot（旧调用方 / 用户手描策略）→ NULL，不伪造血缘。"""
    got = client.get(f"/strategy_candidates/{candidate_id}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["factor_snapshot"] is None


@pytest.mark.asyncio
async def test_factor_snapshot_idempotent_hit_keeps_original(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """同 code 二次提交带不同 snapshot → 命中幂等返老行，血缘不被改写。"""
    salt = uuid.uuid4().hex[:8]
    code = _MIN_STRATEGY + f'\n"idempotent lineage salt {salt}"\n'
    first = {"venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h", "factors": []}
    r1 = client.post(
        "/strategy_candidates",
        headers=auth_headers,
        json={"code": code, "description": "v1", "factor_snapshot": first},
    )
    assert r1.status_code == 200 and r1.json()["created"] is True

    r2 = client.post(
        "/strategy_candidates",
        headers=auth_headers,
        json={
            "code": code,
            "description": "v2",
            "factor_snapshot": {**first, "venue": "yfinance"},
        },
    )
    assert r2.status_code == 200
    assert r2.json()["created"] is False
    assert r2.json()["candidate_id"] == r1.json()["candidate_id"]

    got = client.get(
        f"/strategy_candidates/{r1.json()['candidate_id']}", headers=auth_headers
    )
    assert got.json()["factor_snapshot"]["venue"] == "binance"
