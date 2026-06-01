"""D-9.1a 真 ``RiskGuardFactory`` + ``PostgresTradeRepository`` 端到端测试。

issue #8 验收：用默认 ``risk_rules.toml`` + lifespan 构造的真 factory + 往
``closed_trades`` 表注入历史，HTTP POST /orders/submit 应被对应 RiskRule 拦截。

覆盖：

- CooldownRule（lookback 5 min 内同 symbol 平仓 → 锁 5 min）
- StoplossGuardRule（60 min 内 5 笔止损 → 全局锁 2h）
- LowProfitRule（12h 内 4 笔同 side 累计 < -5% → 该 symbol-side 锁 1h）
- MaxDrawdownRule（24h 内 ≥5 笔 + equity 回撤 > 15% → 全局锁 4h）

MarketHoursRule：规则正确性见 ``test_market_calendar.py``；本套另补一对 e2e
（monkeypatch ``now`` 注入周六 → 美股闭市拦截 / crypto 放行）确认 HTTP 全链路。

测试隔离：每条用例用 ``fresh_user`` fixture 拿独立 sub → 独立 account_id；
注入的 closed_trades 仅影响该 account；factory LRU cache 也会按 account 分桶。
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import inalpha_shared.db as shared_db
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

from inalpha_paper.account_id import account_id_from_sub
from inalpha_paper.storage import closed_trades as ct_store

# 注意：``inalpha_paper.main`` 在 module top-level 调 ``get_paper_settings()`` 缓存 settings；
# 若本文件 top-level import 它，会在 conftest ``_ensure_env`` session-fixture 跑之前
# 锁定 .env 的 DATA_SERVICE_URL=localhost:8001，污染下游用 respx mock "data-mock.test"
# 的测试。所以 ``_build_risk_guard_factory`` lazy import 在 fixture 里。

pytestmark = pytest.mark.integration


# ────────────────────────────────────────────────────────────────────
# fixtures
# ────────────────────────────────────────────────────────────────────


def _get_pool() -> Any:
    if shared_db._pool is None:
        raise RuntimeError("DB pool not initialized (lifespan must run first)")
    return shared_db._pool


@pytest_asyncio.fixture
async def real_factory(app_with_lifespan: Any) -> AsyncIterator[Any]:
    """重新用真 lifespan 逻辑构造 RiskGuardFactory，覆盖 conftest 的 None 隔离。

    autouse fixture ``_isolate_risk_state_in_tests`` 把 factory 置 None；本 fixture
    在每个用例上层重建真 factory，让 enforce 走完整路径（PostgresTradeRepository +
    RoutingCalendar）。
    """
    # 见文件顶部 import 注释：lazy import 避免污染 settings cache
    from inalpha_paper.main import _build_risk_guard_factory

    factory = await _build_risk_guard_factory(_get_pool())
    assert factory is not None, "TOML 加载失败 — 检查 configs/risk_rules.toml"
    app_with_lifespan.state.risk_guard_factory = factory
    yield factory


@pytest_asyncio.fixture(autouse=True)
async def _clean_closed_trades(app_with_lifespan: Any) -> AsyncIterator[None]:
    """每个 e2e 测试前后清空 closed_trades + risk_locks，避免跨 test 污染。"""
    del app_with_lifespan
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("TRUNCATE TABLE closed_trades RESTART IDENTITY")
            await cur.execute("TRUNCATE TABLE risk_locks RESTART IDENTITY")
        await conn.commit()
    yield
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("TRUNCATE TABLE closed_trades RESTART IDENTITY")
            await cur.execute("TRUNCATE TABLE risk_locks RESTART IDENTITY")
        await conn.commit()


async def _insert_trade(
    *,
    account_id: Any,
    venue: str,
    symbol: str,
    side: str,
    close_ts: datetime,
    profit_pct: float,
    profit_abs: float,
    exit_reason: str = "manual",
) -> None:
    """Helper：往 closed_trades 表注入一笔。"""
    async with get_conn() as conn:
        await ct_store.insert_close(
            conn,
            account_id=account_id,
            venue=venue,
            symbol=symbol,
            side=side,
            open_ts=close_ts - timedelta(minutes=5),
            close_ts=close_ts,
            open_price=Decimal("50000"),
            close_price=Decimal(str(50_000 * (1 + profit_pct))),
            quantity=Decimal("0.01"),
            close_profit_pct=profit_pct,
            close_profit_abs=profit_abs,
            exit_reason=exit_reason,
            open_order_id=f"test-open-{close_ts.isoformat()}",
            close_order_id=f"test-close-{close_ts.isoformat()}",
        )
        await conn.commit()


def _submit_btc_buy(
    client: TestClient, headers: dict[str, str], *, ref_price: float = 50_000.0
) -> Any:
    """POST /orders/submit BTC/USDT@binance BUY 0.01。"""
    return client.post(
        "/orders/submit",
        headers=headers,
        json={
            "symbol": "BTC/USDT",
            "venue": "binance",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": ref_price,
        },
    )


def _submit_btc_sell(
    client: TestClient, headers: dict[str, str], *, ref_price: float = 50_000.0
) -> Any:
    return client.post(
        "/orders/submit",
        headers=headers,
        json={
            "symbol": "BTC/USDT",
            "venue": "binance",
            "side": "SELL",
            "type": "MARKET",
            "quantity": 0.01,
            "ref_price": ref_price,
        },
    )


def _submit_aapl_buy(
    client: TestClient, headers: dict[str, str], *, ref_price: float = 200.0
) -> Any:
    """POST /orders/submit AAPL@yfinance BUY 1。"""
    return client.post(
        "/orders/submit",
        headers=headers,
        json={
            "symbol": "AAPL",
            "venue": "yfinance",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 1,
            "ref_price": ref_price,
        },
    )


# ────────────────────────────────────────────────────────────────────
# CooldownRule（lookback=5, stop_duration=5）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooldown_rule_triggers_after_recent_close(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
) -> None:
    """3 分钟前同 symbol 平仓 → CooldownRule 拦下次单。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    account_id = account_id_from_sub(fresh_user["sub"])

    # 3 分钟前 BTC/USDT 平仓
    await _insert_trade(
        account_id=account_id,
        venue="binance",
        symbol="BTC/USDT",
        side="long",
        close_ts=datetime.now(UTC) - timedelta(minutes=3),
        profit_pct=0.01,
        profit_abs=10.0,
        exit_reason="manual",
    )

    r = _submit_btc_buy(client, headers)
    assert r.status_code == 409, r.json()
    body = r.json()
    assert body["code"] == "RISK_REJECTED"
    assert body["details"]["rule_name"] == "CooldownRule"
    assert body["details"]["lock_scope"] == "symbol"


@pytest.mark.asyncio
async def test_cooldown_rule_passes_when_close_outside_window(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
) -> None:
    """10 分钟前平仓（> lookback 5 min）→ Cooldown 不触发。其他 rule 也不触发，应成交。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    account_id = account_id_from_sub(fresh_user["sub"])

    await _insert_trade(
        account_id=account_id,
        venue="binance",
        symbol="BTC/USDT",
        side="long",
        close_ts=datetime.now(UTC) - timedelta(minutes=10),
        profit_pct=0.01,
        profit_abs=10.0,
    )

    r = _submit_btc_buy(client, headers)
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "FILLED"


# ────────────────────────────────────────────────────────────────────
# StoplossGuardRule（trade_limit=5, lookback=60 min, required_profit=0.0）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stoploss_guard_triggers_after_5_losses_in_window(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
) -> None:
    """60 min 内 5 笔 stop_loss 平仓（profit_pct < 0）→ StoplossGuardRule 全局锁。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    account_id = account_id_from_sub(fresh_user["sub"])

    now = datetime.now(UTC)
    # 5 笔止损均匀分布在过去 30 分钟内
    for i in range(5):
        await _insert_trade(
            account_id=account_id,
            venue="binance",
            symbol="BTC/USDT",
            side="long",
            close_ts=now - timedelta(minutes=30 - i * 5),
            profit_pct=-0.02,
            profit_abs=-100.0,
            exit_reason="stop_loss",
        )

    # CooldownRule 也会因最近平仓拦，所以我们等过冷却（让最后一笔距离 now > 5min）
    # 已上面写的 i=4 的 close_ts = now - 10min → 出 cooldown 窗口
    # 但 stoploss_guard lookback=60 包含全部 5 笔 → 仍触发

    r = _submit_btc_buy(client, headers)
    assert r.status_code == 409, r.json()
    body = r.json()
    assert body["code"] == "RISK_REJECTED"
    assert body["details"]["rule_name"] == "StoplossGuardRule"
    assert body["details"]["lock_scope"] == "global"


@pytest.mark.asyncio
async def test_stoploss_guard_global_lock_blocks_other_symbols(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
) -> None:
    """StoplossGuard global 锁触发后，同账户其他 symbol 也被拦（命中现有 global 锁）。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    account_id = account_id_from_sub(fresh_user["sub"])
    now = datetime.now(UTC)

    for i in range(5):
        await _insert_trade(
            account_id=account_id,
            venue="binance",
            symbol="BTC/USDT",
            side="long",
            close_ts=now - timedelta(minutes=30 - i * 5),
            profit_pct=-0.02,
            profit_abs=-100.0,
            exit_reason="stop_loss",
        )

    # 第一次触发 global 锁
    r1 = _submit_btc_buy(client, headers)
    assert r1.status_code == 409
    assert r1.json()["details"]["rule_name"] == "StoplossGuardRule"

    # 不同 symbol（ETH/USDT）同账户 → 命中现有 global 锁
    r2 = client.post(
        "/orders/submit",
        headers=headers,
        json={
            "symbol": "ETH/USDT",
            "venue": "binance",
            "side": "BUY",
            "type": "MARKET",
            "quantity": 0.1,
            "ref_price": 3_000.0,
        },
    )
    assert r2.status_code == 409
    assert r2.json()["details"]["from_existing_lock"] is True


# ────────────────────────────────────────────────────────────────────
# LowProfitRule（trade_limit=4, required_profit=-0.05, only_per_side=true）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_low_profit_rule_triggers_for_same_side(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
) -> None:
    """12h 内 4 笔同 long-side 累计 < -5%（4 × -2% = -8%）→ LowProfitRule 锁 long。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    account_id = account_id_from_sub(fresh_user["sub"])
    now = datetime.now(UTC)

    # 4 笔 long，每笔 -2%，落在 12h 窗口内（同时避开 cooldown 5min 窗口与
    # stoploss_guard 5/60min — 这里 close_ts 都在 > 10min 外，且 4 < 5
    # stoploss trade_limit，所以只 LowProfit 命中）
    for i in range(4):
        await _insert_trade(
            account_id=account_id,
            venue="binance",
            symbol="BTC/USDT",
            side="long",
            close_ts=now - timedelta(minutes=60 + i * 30),
            profit_pct=-0.02,
            profit_abs=-100.0,
            exit_reason="manual",  # 非 stop_loss → 不计入 StoplossGuard
        )

    # BUY → side="long" → 触发
    r_buy = _submit_btc_buy(client, headers)
    assert r_buy.status_code == 409, r_buy.json()
    assert r_buy.json()["details"]["rule_name"] == "LowProfitRule"
    assert r_buy.json()["details"]["lock_scope"] == "symbol"


@pytest.mark.asyncio
async def test_low_profit_rule_does_not_block_opposite_side(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
) -> None:
    """4 笔 long 亏损不应锁 short。only_per_side=true 时方向独立。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    account_id = account_id_from_sub(fresh_user["sub"])
    now = datetime.now(UTC)

    for i in range(4):
        await _insert_trade(
            account_id=account_id,
            venue="binance",
            symbol="BTC/USDT",
            side="long",
            close_ts=now - timedelta(minutes=60 + i * 30),
            profit_pct=-0.02,
            profit_abs=-100.0,
            exit_reason="manual",
        )

    # SELL → side="short" → LowProfit only_per_side=true 不锁 short
    r_sell = _submit_btc_sell(client, headers)
    assert r_sell.status_code == 200, r_sell.json()
    assert r_sell.json()["status"] == "FILLED"


# ────────────────────────────────────────────────────────────────────
# MaxDrawdownRule（max_drawdown=0.15, lookback=1440 min, trade_limit=5）
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_drawdown_rule_triggers_when_equity_drops_15pct(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
) -> None:
    """24h 内 5 笔累计 abs profit -2000 USD（starting=10000）→ drawdown 20% > 15% → 锁。

    避开 stoploss_guard（exit_reason=manual 不计入）+ low_profit（exit_reason 不影响，
    但 profit_pct 选小数 -1% × 5 = -5% 不会撞 LowProfit -5% threshold（< 不是 <=））+
    cooldown（最后一笔 > 5min）。
    """
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    account_id = account_id_from_sub(fresh_user["sub"])
    now = datetime.now(UTC)

    # 5 笔 abs -400 USD 各 → 累计 -2000 → drawdown = (10000 - 8000) / 10000 = 20%
    # profit_pct=-0.01 让 LowProfit cumulative=-5% **不严格小于** -5%（>=，不触发）
    for i in range(5):
        await _insert_trade(
            account_id=account_id,
            venue="binance",
            symbol="BTC/USDT",
            side="long",
            close_ts=now - timedelta(minutes=120 + i * 60),  # 2h-7h ago
            profit_pct=-0.01,
            profit_abs=-400.0,
            exit_reason="manual",  # 不计入 StoplossGuard
        )

    r = _submit_btc_buy(client, headers)
    assert r.status_code == 409, r.json()
    body = r.json()
    assert body["code"] == "RISK_REJECTED"
    assert body["details"]["rule_name"] == "MaxDrawdownRule"
    assert body["details"]["lock_scope"] == "global"


# ────────────────────────────────────────────────────────────────────
# Sanity：无历史 trade → 全 pass
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_history_passes_all_rules(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
) -> None:
    """干净账户 + crypto venue（避开 MarketHours）→ 所有 trade-based rule 都不触发。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}

    r = _submit_btc_buy(client, headers)
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "FILLED"


# ────────────────────────────────────────────────────────────────────
# MarketHoursRule（多市场 · D-9.1a issue #8）—— monkeypatch now 注入确定性时刻
# ────────────────────────────────────────────────────────────────────


def _freeze_now(monkeypatch: pytest.MonkeyPatch, frozen: datetime) -> None:
    """把 ``risk_guard.enforce`` 里的 ``datetime.now`` 冻结到 ``frozen``。

    ``enforce`` 写死 ``datetime.now(UTC)``（服务器真实时间），MarketHoursRule 因此
    无法靠请求注入时间。这里 monkeypatch ``risk_guard.datetime`` 为只覆写 ``now`` 的
    子类，让 e2e 能确定性命中闭市时段（不依赖跑测试时的真实墙钟）。
    """
    import inalpha_paper.execution.risk_guard as rg

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return frozen

    monkeypatch.setattr(rg, "datetime", _FrozenDatetime)


@pytest.mark.asyncio
async def test_market_hours_blocks_us_equity_when_closed(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-05-30 周六 → 美股闭市 → AAPL@yfinance 被 MarketHoursRule 拦（交易所解析 XNYS）。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    _freeze_now(monkeypatch, datetime(2026, 5, 30, 15, 0, tzinfo=UTC))  # 周六

    r = _submit_aapl_buy(client, headers)
    assert r.status_code == 409, r.json()
    body = r.json()
    assert body["code"] == "RISK_REJECTED"
    assert body["details"]["rule_name"] == "MarketHoursRule"
    assert body["details"]["lock_scope"] == "market"
    # details 不含 lock_market 字段，但 reason 带解析出的交易所 code
    assert "XNYS" in body["details"]["reason"]


@pytest.mark.asyncio
async def test_market_hours_allows_crypto_when_us_closed(
    client: TestClient,
    fresh_user: dict[str, str],
    real_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一周六，crypto 24/7 → BTC@binance 不被 MarketHoursRule 拦（无历史 → 成交）。"""
    del real_factory
    headers = {"Authorization": fresh_user["Authorization"]}
    _freeze_now(monkeypatch, datetime(2026, 5, 30, 15, 0, tzinfo=UTC))  # 周六

    r = _submit_btc_buy(client, headers)
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "FILLED"
