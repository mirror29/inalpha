"""历史 orders → closed_trades backfill 脚本（ADR-0007 Slice 7）。

ADR-0007 落地后，**历史 orders** 不会自动派生 closed_trades 记录。本脚本一次性
从 orders 表重放 fill 序列 + 检测 close + INSERT closed_trades，让上线时 trade-based
RiskRule 即时拥有完整的历史数据。

用法：

    # Dry-run（默认）：只统计不写
    uv run python -m inalpha_paper.scripts.backfill_closed_trades \\
        --account-id <uuid> --dry-run

    # 真写
    uv run python -m inalpha_paper.scripts.backfill_closed_trades \\
        --account-id <uuid>

    # 限定 venue / symbol
    uv run python -m inalpha_paper.scripts.backfill_closed_trades \\
        --account-id <uuid> --venue binance --symbol BTC/USDT

约束：

- **exit_reason 全是 'signal'**：历史 Order 无 tag 字段（ADR-0007 之前没加）
- 幂等性：本脚本**不删旧 closed_trades**——重跑会重复 INSERT。请确认 closed_trades
  表为空（或先 DELETE）再跑。
- 仅处理 status='FILLED' 订单（PARTIALLY_FILLED 暂不重放）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from inalpha_shared.db import close_pool, get_conn, init_pool

from inalpha_paper.engine.close_detector import ClosedTradeStaging
from inalpha_paper.kernel.identifiers import InstrumentId
from inalpha_paper.model.orders import OrderSide
from inalpha_paper.model.positions import Position

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BackfillStats:
    accounts_scanned: int = 0
    fills_replayed: int = 0
    closes_detected: int = 0
    closes_written: int = 0

    def __str__(self) -> str:
        return (
            f"accounts={self.accounts_scanned} fills={self.fills_replayed} "
            f"closes_detected={self.closes_detected} closes_written={self.closes_written}"
        )


async def _list_filled_orders(
    conn: Any,
    *,
    account_id: UUID,
    venue: str | None,
    symbol: str | None,
    since: datetime | None,
) -> list[dict[str, Any]]:
    """按 (venue, symbol, ts_event) 升序读 FILLED 订单。"""
    sql = (
        "SELECT client_order_id, venue, symbol, side, "
        "filled_quantity AS qty, avg_fill_price AS price, ts_event "
        "FROM orders WHERE account_id = %s AND status = 'FILLED' "
        "AND filled_quantity > 0"
    )
    params: list[Any] = [str(account_id)]
    if venue is not None:
        sql += " AND venue = %s"
        params.append(venue)
    if symbol is not None:
        sql += " AND symbol = %s"
        params.append(symbol)
    if since is not None:
        sql += " AND ts_event >= %s"
        params.append(since)
    sql += " ORDER BY venue, symbol, ts_event"

    async with conn.cursor() as cur:
        await cur.execute(sql, tuple(params))
        rows = await cur.fetchall()
    return list(rows)


def _replay_account(
    rows: list[dict[str, Any]], account_id: UUID
) -> list[ClosedTradeStaging]:
    """重放单账户的 fill 序列。返回所有 close 出来的 staging。"""
    from inalpha_paper.engine.close_detector import detect_close

    # 按 (venue, symbol) 维护单独的 Position
    positions: dict[tuple[str, str], Position] = {}
    closes: list[ClosedTradeStaging] = []

    for row in rows:
        venue = row["venue"]
        symbol = row["symbol"]
        key = (venue, symbol)
        instrument_id = InstrumentId(symbol=symbol, venue=venue)

        if key not in positions:
            positions[key] = Position(instrument_id=instrument_id)
        pos = positions[key]

        side = OrderSide.BUY if row["side"] == "BUY" else OrderSide.SELL
        qty = float(row["qty"])
        price = float(row["price"])
        ts_event_dt: datetime = row["ts_event"]
        ts_ns = int(ts_event_dt.timestamp() * 1_000_000_000)

        # 构造伪 OrderFilled 给 detect_close（tag=None → exit_reason='signal'）
        from inalpha_paper.kernel.identifiers import ClientOrderId, StrategyId, VenueOrderId
        from inalpha_paper.model.events import OrderFilled

        fake_fill = OrderFilled(
            client_order_id=ClientOrderId(str(row["client_order_id"])),
            strategy_id=StrategyId("backfill"),
            ts_event=ts_ns,
            ts_init=ts_ns,
            venue_order_id=VenueOrderId(""),
            instrument_id=instrument_id,
            side=side,
            fill_quantity=qty,
            fill_price=price,
            trade_id="",
            is_last_fill=True,
            tag=None,
        )

        # detect 必须**在** apply_fill 之前
        staging = detect_close(pos, fake_fill, account_id=account_id)
        if staging is not None:
            closes.append(staging)

        pos.apply_fill(
            side, qty, price, ts_ns,
            open_order_id=str(row["client_order_id"]),
        )

    return closes


async def _write_closes(
    conn: Any, closes: list[ClosedTradeStaging]
) -> int:
    """批量写 closed_trades。返回写入条数。"""
    from inalpha_paper.storage import closed_trades as trades_store

    written = 0
    for s in closes:
        await trades_store.insert_close(
            conn,
            account_id=s.account_id,
            venue=s.venue,
            symbol=s.symbol,
            side=s.side,
            open_ts=s.open_ts,
            close_ts=s.close_ts,
            open_price=Decimal(s.open_price),
            close_price=Decimal(s.close_price),
            quantity=Decimal(s.quantity),
            close_profit_pct=s.close_profit_pct,
            close_profit_abs=s.close_profit_abs,
            exit_reason=s.exit_reason,
            open_order_id=s.open_order_id,
            close_order_id=s.close_order_id,
        )
        written += 1
    await conn.commit()
    return written


async def run_backfill(
    *,
    account_id: UUID,
    venue: str | None = None,
    symbol: str | None = None,
    since: datetime | None = None,
    dry_run: bool = True,
) -> BackfillStats:
    """主入口。**调用方需自己 init_pool / close_pool**（或用 `main` 包装）。"""
    stats = BackfillStats(accounts_scanned=1)

    async with get_conn() as conn:
        rows = await _list_filled_orders(
            conn,
            account_id=account_id,
            venue=venue,
            symbol=symbol,
            since=since,
        )
    stats.fills_replayed = len(rows)

    closes = _replay_account(rows, account_id)
    stats.closes_detected = len(closes)

    if dry_run:
        logger.info("dry-run mode; %d closes detected, NOT writing to DB", len(closes))
        return stats

    async with get_conn() as conn:
        stats.closes_written = await _write_closes(conn, closes)
    return stats


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", type=UUID, required=True)
    parser.add_argument("--venue", default=None)
    parser.add_argument("--symbol", default=None)
    parser.add_argument(
        "--since",
        default=None,
        help="ISO 8601 datetime（含时区）lower bound on order ts_event",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="(default) 只统计不写，确认数据量后再去掉本 flag",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="真写入 closed_trades 表",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        logger.error("DATABASE_URL not set")
        return 2

    since: datetime | None = None
    if args.since is not None:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=UTC)

    await init_pool(db_url)
    try:
        stats = await run_backfill(
            account_id=args.account_id,
            venue=args.venue,
            symbol=args.symbol,
            since=since,
            dry_run=args.dry_run,
        )
        logger.info("backfill done: %s (dry_run=%s)", stats, args.dry_run)
    finally:
        await close_pool()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
