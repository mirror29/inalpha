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
    """每个候选用**结构可区分**的 code，避免去重导致测试间串扰。

    salt 作唯一 STRING 字面量（不能用注释）：insert_candidate 的结构指纹去重
    （compute_structure_hash）剥注释后再 hash，注释 salt 会让所有候选结构相同 →
    dedup 成同一个 → 第二次起跑撞 UNIQUE(candidate_id) running。STRING 字面量被
    结构指纹保留，故每个候选结构唯一。"""
    return _MINIMAL_CODE + f'\n"structural salt {uuid4().hex}"\n'


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


def _stub_manager_async(app: Any) -> list[dict[str, Any]]:
    """把 manager.start_async 换成记录调用的 no-op async。"""
    started: list[dict[str, Any]] = []

    async def _rec(run: dict[str, Any]) -> None:
        started.append(run)

    app.state.live_runner_manager.start_async = _rec
    app.state.live_runner_manager.start = lambda run: started.append(run)
    return started


async def test_start_perp_on_non_crypto_rejected(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """perp 仅 crypto:非 crypto venue 起 perp run → 422 PERP_NOT_ELIGIBLE。"""
    _stub_manager_async(app_with_lifespan)
    cid = await _make_promoted_candidate()
    r = client.post(
        "/strategy_runs",
        headers=_headers(client),
        json={"candidate_id": str(cid), "venue": "yfinance", "symbol": "AAPL",
              "timeframe": "1h", "trading_mode": "perp", "leverage": 2},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "PERP_NOT_ELIGIBLE"


async def test_start_perp_eligible_carries_mode(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """crypto 永续 perp run → 200,响应带 trading_mode=perp/leverage,且透传给 manager。"""
    started = _stub_manager_async(app_with_lifespan)
    cid = await _make_promoted_candidate()
    r = client.post(
        "/strategy_runs",
        headers=_headers(client),
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT:USDT",
              "timeframe": "1h", "trading_mode": "perp", "leverage": 5},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["trading_mode"] == "perp"
    assert body["leverage"] == 5
    assert len(started) == 1 and started[0]["trading_mode"] == "perp"


async def test_start_perp_long_only_strategy_warns(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """perp + 疑似 long-only 策略(_is_long 无 _is_short)→ 放行但 run_log 软告警。"""
    _stub_manager_async(app_with_lifespan)
    async with get_conn() as conn:
        cid, _ = await candidates_store.insert_candidate(
            conn, code='self._is_long = False\n"long-only salt ' + uuid4().hex + '"\n'
        )
        await candidates_store.set_status(conn, cid, "promoted")
    r = client.post(
        "/strategy_runs",
        headers=_headers(client),
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT:USDT",
              "timeframe": "1h", "trading_mode": "perp", "leverage": 3},
    )
    assert r.status_code == 200, r.json()
    async with get_conn() as conn:
        run = await runs_store.get(conn, UUID(r.json()["id"]))
    msgs = " ".join(e.get("msg", "") for e in (run["run_log"] or []))
    assert "long-only" in msgs and "做空" in msgs


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
    # 默认 allocation = min(10000, 账户折算可用现金);新账户即 10000,落库并回显
    assert body["allocation"] == 10_000.0
    assert len(started) == 1  # manager.start 被调用

    # 同 candidate 第二个 running → 409(换 symbol 避开同标的守门、显式 allocation
    # 避开自动额度 422,专测 candidate 唯一性)
    r2 = client.post(
        "/strategy_runs",
        headers=headers,
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "ETH/USDT",
              "timeframe": "1h", "allocation": 1_000.0},
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "STRATEGY_RUN_ALREADY_RUNNING"


async def test_same_symbol_second_run_conflict(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """同账户同 (venue, symbol) 第二个 run → 409 SYMBOL_RUN_CONFLICT(issue #108)。

    positions 一标的一行,两个 run 撞同标的会共享持仓互相打架 → start 即拒。
    """
    _stub_manager(app_with_lifespan)
    headers = _headers(client)
    cid1 = await _make_promoted_candidate()
    r1 = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid1), "venue": "binance", "symbol": "SOL/USDT", "timeframe": "1h"},
    )
    assert r1.status_code == 200, r1.json()

    # 不同 candidate、同 venue+symbol → 被同标的守门拒
    cid2 = await _make_promoted_candidate()
    r2 = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid2), "venue": "binance", "symbol": "SOL/USDT", "timeframe": "1h"},
    )
    assert r2.status_code == 409, r2.json()
    assert r2.json()["code"] == "SYMBOL_RUN_CONFLICT"

    # 换 symbol 即可正常 start(守门只按标的,不锁账户)。显式 allocation:首个 run
    # 已把默认未分配额度(1 万)占满,自动额度会 422(见 allocation 扣减测试)。
    r3 = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid2), "venue": "binance", "symbol": "ETH/USDT",
              "timeframe": "1h", "allocation": 2_000.0},
    )
    assert r3.status_code == 200, r3.json()


async def test_auto_allocation_deducts_running_runs(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """自动 allocation 扣减其他 running run 已分配额度,防集体超额认领资本。

    账户 1 万:首个 run 显式占 4000;第二个自动额度 = min(10000, 10000−4000) = 6000;
    第三个自动额度 = 0 → 422(现金没花出去也不能再虚拟认领)。
    """
    _stub_manager(app_with_lifespan)
    headers = _headers(client)
    cid1 = await _make_promoted_candidate()
    r1 = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid1), "venue": "binance", "symbol": "BTC/USDT",
              "timeframe": "1h", "allocation": 4_000.0},
    )
    assert r1.status_code == 200, r1.json()

    cid2 = await _make_promoted_candidate()
    r2 = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid2), "venue": "binance", "symbol": "ETH/USDT", "timeframe": "1h"},
    )
    assert r2.status_code == 200, r2.json()
    assert r2.json()["allocation"] == pytest.approx(6_000.0)

    cid3 = await _make_promoted_candidate()
    r3 = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid3), "venue": "binance", "symbol": "SOL/USDT", "timeframe": "1h"},
    )
    assert r3.status_code == 422, r3.json()
    assert r3.json()["code"] == "INSUFFICIENT_CASH_FOR_RUN"
    assert float(r3.json()["details"]["already_allocated"]) == pytest.approx(10_000.0)


async def test_explicit_allocation_recorded(
    client: TestClient, app_with_lifespan: Any
) -> None:
    """显式 allocation 原样落库回显(可大于账户可用——下单时账户级硬底会拒单,start 不拦)。"""
    started = _stub_manager(app_with_lifespan)
    cid = await _make_promoted_candidate()
    r = client.post(
        "/strategy_runs", headers=_headers(client),
        json={"candidate_id": str(cid), "venue": "binance", "symbol": "BTC/USDT",
              "timeframe": "1h", "allocation": 2_500.0},
    )
    assert r.status_code == 200, r.json()
    assert r.json()["allocation"] == 2_500.0
    # manager 收到的 run dict 也带 allocation(runner 用它当 session 虚拟钱包)
    assert len(started) == 1
    assert float(started[0]["allocation"]) == 2_500.0


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

    # 起满 2 个（不同 candidate + 不同 symbol 避开同标的守门;显式 allocation 避开
    # 自动额度扣减——本测试只测数量上限）
    for symbol in ("BTC/USDT", "ETH/USDT"):
        cid = await _make_promoted_candidate(owner_account_id=acct)
        r = client.post(
            "/strategy_runs", headers=headers,
            json={"candidate_id": str(cid), "venue": "binance", "symbol": symbol,
                  "timeframe": "1h", "allocation": 3_000.0},
        )
        assert r.status_code == 200, r.json()

    # 第 3 个 → 429
    cid3 = await _make_promoted_candidate(owner_account_id=acct)
    r = client.post(
        "/strategy_runs", headers=headers,
        json={"candidate_id": str(cid3), "venue": "binance", "symbol": "SOL/USDT",
              "timeframe": "1h", "allocation": 3_000.0},
    )
    assert r.status_code == 429
    assert r.json()["code"] == "TOO_MANY_RUNNING_RUNS"
