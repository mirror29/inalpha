"""``/strategy_runs`` API 测试（D-11）——start / stop / list + 护栏。

stub ``manager.start`` 为 no-op，避免真起后台 task 打网络；只验 API/DB 契约。
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.account_id import account_id_from_sub
from inalpha_paper.config import get_paper_settings
from inalpha_paper.storage import strategy_candidates as candidates_store
from inalpha_paper.storage import strategy_runs as runs_store

from .conftest import fresh_account_token

pytestmark = pytest.mark.integration

# 通过 ast_audit + contract_check 的最小策略
_MINIMAL_CODE = """
class NoopStrategy(Strategy):
    def __init__(self, name, clock, msgbus, instrument_id, timeframe, **kw):
        super().__init__(name, clock, msgbus)
        self._instrument_id = instrument_id
        self._timeframe = timeframe

    def on_start(self):
        self.subscribe_bars(self._instrument_id, self._timeframe)

    def on_bar(self, bar):
        pass
"""


def _unique_code() -> str:
    """每个候选用唯一 code（加 salt 注释），避免 code_hash 去重导致测试间串扰。"""
    return _MINIMAL_CODE + f"\n# salt: {uuid4().hex}\n"


async def _make_promoted_candidate(owner_account_id: UUID | None = None) -> UUID:
    """直接落库 + set_status('promoted')，绕过 backtest/promote 端点守门。

    owner_account_id=None → owner 列 NULL（模拟 pre-migration 老数据 / 归属校验放行）。
    """
    async with get_conn() as conn:
        cid, _created = await candidates_store.insert_candidate(
            conn, code=_unique_code(), owner_account_id=owner_account_id
        )
        await candidates_store.set_status(conn, cid, "promoted")
    return cid


def _stub_manager(app: Any) -> list[dict[str, Any]]:
    """把 manager.start 换成记录调用的 no-op，返回记录列表。"""
    started: list[dict[str, Any]] = []
    app.state.live_runner_manager.start = lambda run: started.append(run)
    return started


def _headers(client: TestClient) -> dict[str, str]:
    from .conftest import fresh_account_token

    _, token = fresh_account_token("run")
    return {"Authorization": f"Bearer {token}"}


async def test_start_requires_promoted_candidate(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """非 promoted candidate → 422 CANDIDATE_NOT_PROMOTED。"""
    _stub_manager(app_with_lifespan)
    async with get_conn() as conn:
        cid, _ = await candidates_store.insert_candidate(conn, code=_unique_code())
    r = client.post(
        "/strategy_runs",
        headers=_headers(client),
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "CANDIDATE_NOT_PROMOTED"


async def test_start_requires_venue(client: TestClient, app_with_lifespan: Any) -> None:
    """venue 必填（不预设市场，CLAUDE.md §3）：缺 venue → 422 请求校验错误。"""
    _stub_manager(app_with_lifespan)
    cid = await _make_promoted_candidate()
    r = client.post(
        "/strategy_runs",
        headers=_headers(client),
        json={"candidate_id": str(cid), "symbol": "BTC/USDT", "timeframe": "1h"},  # 无 venue
    )
    assert r.status_code == 400  # 请求体校验失败（本服务把 pydantic 校验映射成 400），不是 binance 静默兜底


async def test_start_happy_path_and_duplicate(
    client: TestClient, app_with_lifespan: Any
) -> None:
    started = _stub_manager(app_with_lifespan)
    cid = await _make_promoted_candidate()
    headers = _headers(client)

    r = client.post(
        "/strategy_runs",
        headers=headers,
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["status"] == "running"
    assert body["symbol"] == "BTC/USDT"
    assert len(started) == 1  # manager.start 被调用

    # 同 candidate 第二个 running → 409
    r2 = client.post(
        "/strategy_runs",
        headers=headers,
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "STRATEGY_RUN_ALREADY_RUNNING"


async def test_list_invalid_status_rejected(client: TestClient, app_with_lifespan: Any) -> None:
    """status 传非法值 → 请求校验失败（不静默返空列表）。"""
    _stub_manager(app_with_lifespan)
    r = client.get("/strategy_runs?status=INVALID", headers=_headers(client))
    assert r.status_code in (400, 422)  # Literal 校验，非 200+[]


async def test_stop_and_list(client: TestClient, app_with_lifespan: Any) -> None:
    _stub_manager(app_with_lifespan)
    # stop 用 manager.stop —— 也 stub 掉（只改 DB 状态）
    async def _fake_stop(run_id: UUID) -> None:
        async with get_conn() as conn:
            from inalpha_paper.storage import strategy_runs as runs_store
            await runs_store.set_status(conn, run_id, "stopped")
    app_with_lifespan.state.live_runner_manager.stop = _fake_stop

    cid = await _make_promoted_candidate()
    headers = _headers(client)
    run = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    ).json()

    # list 能看到 running
    lst = client.get("/strategy_runs", headers=headers).json()
    assert any(r["id"] == run["id"] and r["status"] == "running" for r in lst)

    # stop → status stopped
    stopped = client.post(f"/strategy_runs/{run['id']}/stop", headers=headers).json()
    assert stopped["status"] == "stopped"


async def test_list_decisions_and_ownership(client: TestClient, app_with_lifespan: Any) -> None:
    """GET /strategy_runs/{id}/decisions 返回复盘时间线 + 归属校验。"""
    _stub_manager(app_with_lifespan)
    cid = await _make_promoted_candidate()
    headers = _headers(client)
    run = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    ).json()

    # 直接落一行决策（绕过 runner）
    async with get_conn() as conn:
        await runs_store.insert_decision(
            conn, run_id=UUID(run["id"]), bar_ts=datetime(2026, 6, 2, tzinfo=UTC),
            bar_close=Decimal("50000"), side="BUY", quantity=Decimal("0.01"),
            order_type="MARKET", outcome="filled", fill_price=Decimal("50000"),
            fee=Decimal("0.5"), order_id="ord-x",
        )

    r = client.get(f"/strategy_runs/{run['id']}/decisions", headers=headers)
    assert r.status_code == 200, r.json()
    body = r.json()
    assert len(body) == 1
    assert body[0]["outcome"] == "filled"
    assert body[0]["side"] == "BUY"
    assert body[0]["order_id"] == "ord-x"

    # 别的账户拉别人的 decisions → 404
    r2 = client.get(f"/strategy_runs/{run['id']}/decisions", headers=_headers(client))
    assert r2.status_code == 404


async def test_stop_other_account_run_404(client: TestClient, app_with_lifespan: Any) -> None:
    """停别人账户的 run → 404（归属校验）。"""
    _stub_manager(app_with_lifespan)
    cid = await _make_promoted_candidate()
    run = client.post(
        "/strategy_runs", headers=_headers(client),
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    ).json()
    # 另一个用户来 stop
    r = client.post(f"/strategy_runs/{run['id']}/stop", headers=_headers(client))
    assert r.status_code == 404


async def test_start_other_account_candidate_forbidden(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """不能挂别人 promote 的 candidate 在自己账户跑 → 403 CANDIDATE_NOT_OWNED（issue #36.1）。"""
    _stub_manager(app_with_lifespan)
    owner_sub, _ = fresh_account_token("owner")
    cid = await _make_promoted_candidate(owner_account_id=account_id_from_sub(owner_sub))
    # 另一个账户（_headers 每次新 token）来 start
    r = client.post(
        "/strategy_runs", headers=_headers(client),
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    )
    assert r.status_code == 403
    assert r.json()["code"] == "CANDIDATE_NOT_OWNED"


async def test_start_own_candidate_ok(client: TestClient, app_with_lifespan: Any) -> None:
    """自己 promote 的 candidate（owner 一致）可以 start。"""
    started = _stub_manager(app_with_lifespan)
    sub, token = fresh_account_token("owner")
    cid = await _make_promoted_candidate(owner_account_id=account_id_from_sub(sub))
    r = client.post(
        "/strategy_runs", headers={"Authorization": f"Bearer {token}"},
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    )
    assert r.status_code == 200, r.json()
    assert len(started) == 1


async def test_start_legacy_null_owner_allowed(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """pre-migration 老数据 owner_account_id=NULL → 放行（有界 fail-open，issue #36.1）。"""
    _stub_manager(app_with_lifespan)
    cid = await _make_promoted_candidate()  # 不传 owner → NULL
    r = client.post(
        "/strategy_runs", headers=_headers(client),
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    )
    assert r.status_code == 200, r.json()


async def test_per_account_run_cap(client: TestClient, app_with_lifespan: Any) -> None:
    """单账户 running run 超上限 → 429 TOO_MANY_RUNNING_RUNS（issue #36.2）。"""
    _stub_manager(app_with_lifespan)
    sub, token = fresh_account_token("capacct")
    acct = account_id_from_sub(sub)
    headers = {"Authorization": f"Bearer {token}"}
    # 把上限压到 2（dependency override，函数级 fixture 不泄漏）
    small = get_paper_settings().model_copy(
        update={"live_max_running_runs_per_account": 2}
    )
    app_with_lifespan.dependency_overrides[get_paper_settings] = lambda: small

    # 起满 2 个（不同 candidate，各归本账户）
    for _ in range(2):
        cid = await _make_promoted_candidate(owner_account_id=acct)
        r = client.post(
            "/strategy_runs", headers=headers,
            json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
        )
        assert r.status_code == 200, r.json()

    # 第 3 个 → 429
    cid3 = await _make_promoted_candidate(owner_account_id=acct)
    r = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid3), "venue": "binance", "symbol": "BTC/USDT", "timeframe": "1h"},
    )
    assert r.status_code == 429
    assert r.json()["code"] == "TOO_MANY_RUNNING_RUNS"
