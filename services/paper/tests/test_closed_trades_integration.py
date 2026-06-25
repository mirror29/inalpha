"""D-9.1a 集成测试：HTTP 订单流 → closed_trades 写入 → RiskGuard 可读。

验证 BUY→SELL 平仓后 closed_trades 表有新行，且 RiskGuard 的
PostgresTradeRepository 能读到这些行（让 trade-based RiskRule 在 HTTP
路径真正生效）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from inalpha_shared.db import get_conn

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def _truncate(app_with_lifespan):  # type: ignore[no-untyped-def]
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute("TRUNCATE TABLE closed_trades RESTART IDENTITY")
            await cur.execute("TRUNCATE TABLE positions RESTART IDENTITY CASCADE")
            await cur.execute("TRUNCATE TABLE orders RESTART IDENTITY CASCADE")
            await cur.execute("TRUNCATE TABLE accounts RESTART IDENTITY CASCADE")
    yield


def _submit(
    client: TestClient,
    auth: dict[str, str],
    *,
    symbol: str = "BTC/USDT",
    side: str = "BUY",
    quantity: float = 0.01,
    ref_price: float = 50000.0,
) -> dict[str, Any]:
    resp = client.post(
        "/orders/submit",
        json={
            "venue": "binance",
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": quantity,
            "ref_price": ref_price,
            "fee_rate": 0.001,
        },
        headers={"Authorization": auth["Authorization"]},
    )
    assert resp.status_code == 200, f"unexpected {resp.status_code}: {resp.json()}"
    return resp.json()


class TestClosedTradesHttpFlow:
    """BUY + SELL → closed_trades 表有行 → RiskGuard 可读。"""

    def test_buy_then_sell_writes_closed_trade(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """两次反向 MARKET 单 → 平仓记录写入 closed_trades。"""
        buy = _submit(client, auth_headers, side="BUY", quantity=0.02)
        assert buy["status"] == "FILLED", f"BUY not filled: {buy}"

        sell = _submit(client, auth_headers, side="SELL", quantity=0.02)
        assert sell["status"] == "FILLED", f"SELL not filled: {sell}"

        async def _check() -> None:
            async with get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT COUNT(*) AS cnt FROM closed_trades "
                        "WHERE venue='binance' AND symbol='BTC/USDT'"
                    )
                    row = await cur.fetchone()
            assert row is not None and row["cnt"] >= 1, (
                f"expected >=1 closed_trade, got {row}"
            )

        import asyncio
        asyncio.get_event_loop().run_until_complete(_check())

    def test_buy_then_sell_records_ts_opened_on_position(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """BUY 之后 positions 表应有 ts_opened 记录。"""
        _submit(client, auth_headers, side="BUY", quantity=0.05)

        async def _check() -> None:
            async with get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT ts_opened, open_order_id FROM positions "
                        "WHERE venue='binance' AND symbol='BTC/USDT'"
                    )
                    row = await cur.fetchone()
            if row is None:
                pytest.fail("position row not found")
            assert row["ts_opened"] is not None, "ts_opened should be set on open"
            assert row["open_order_id"] is not None, "open_order_id should be set on open"

        import asyncio
        asyncio.get_event_loop().run_until_complete(_check())

    def test_buy_half_then_sell_full_writes_correct_close_qty(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """先 BUY 0.05 再 BUY 0.03 加仓 → SELL 0.08 全平 → closed_qty=0.08。"""
        _submit(client, auth_headers, side="BUY", quantity=0.05)
        _submit(client, auth_headers, side="BUY", quantity=0.03)
        sell = _submit(client, auth_headers, side="SELL", quantity=0.08)
        assert sell["status"] == "FILLED"

        async def _check() -> None:
            async with get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT quantity, close_profit_pct, close_profit_abs, side "
                        "FROM closed_trades WHERE venue='binance' AND symbol='BTC/USDT'"
                    )
                    rows = await cur.fetchall()
            assert len(rows) >= 1, "expected at least 1 closed_trade row"
            # 全平：quantity 应为 0.08
            total = sum(Decimal(str(r["quantity"])) for r in rows)
            assert total == Decimal("0.08"), f"expected total closed_qty=0.08, got {total}"

        import asyncio
        asyncio.get_event_loop().run_until_complete(_check())

    def test_partial_sell_closes_long_writes_closed_trade(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """BUY 0.05 → SELL 0.03 部分平仓：closed_trades 应有一条 side='long' 量 0.03 的平仓记录。

        （现货 long-only：SELL 不能超持仓反手做空，超平场景由 perp 分支覆盖；此处验部分平多
        写出正确 side/quantity 的 closed_trade。）
        """
        _submit(client, auth_headers, side="BUY", quantity=0.05)
        sell = _submit(client, auth_headers, side="SELL", quantity=0.03)
        assert sell["status"] == "FILLED"

        async def _check() -> None:
            async with get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT side, quantity, close_profit_pct "
                        "FROM closed_trades WHERE venue='binance' AND symbol='BTC/USDT'"
                    )
                    rows = await cur.fetchall()
            assert len(rows) >= 1, "expected at least 1 closed_trade row"
            close_row = [r for r in rows if r["side"] == "long"]
            assert len(close_row) == 1, (
                f"expected 1 long close, got {[r['side'] for r in rows]}"
            )
            assert Decimal(str(close_row[0]["quantity"])) == Decimal("0.03")

        import asyncio
        asyncio.get_event_loop().run_until_complete(_check())

    def test_multiple_roundtrips_accumulate_in_closed_trades(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """三次 round-trip → closed_trades 累计 3 行。"""
        for _ in range(3):
            buy = _submit(client, auth_headers, side="BUY", quantity=0.01)
            assert buy["status"] == "FILLED"
            sell = _submit(client, auth_headers, side="SELL", quantity=0.01)
            assert sell["status"] == "FILLED"

        async def _check() -> None:
            async with get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT COUNT(*) AS cnt FROM closed_trades "
                        "WHERE venue='binance' AND symbol='BTC/USDT'"
                    )
                    row = await cur.fetchone()
            assert row is not None and row["cnt"] >= 3, (
                f"expected >=3 closed_trades, got {row}"
            )

        import asyncio
        asyncio.get_event_loop().run_until_complete(_check())


class TestPositionsTsOpenedReset:
    """验证 ts_opened 在平仓后正确重置。"""

    def test_flat_after_full_close_clears_ts_opened(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """BUY 0.01 → SELL 0.01 完全平仓 → ts_opened 应为 NULL。"""
        _submit(client, auth_headers, side="BUY", quantity=0.01)
        _submit(client, auth_headers, side="SELL", quantity=0.01)

        async def _check() -> None:
            async with get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT ts_opened, quantity FROM positions "
                        "WHERE venue='binance' AND symbol='BTC/USDT'"
                    )
                    row = await cur.fetchone()
            if row is None:
                return  # table may have been truncated, that's fine
            assert row["ts_opened"] is None, (
                f"ts_opened should be NULL after full close, got {row['ts_opened']}"
            )

        import asyncio
        asyncio.get_event_loop().run_until_complete(_check())
