"""注入合成 closed_trades 模拟历史亏损 → 让 ``StoplossGuardRule`` 真触发拦截。

**Demo / 演示工具**。配合 ``PostgresTradeRepository`` opt-in 模式
（env ``INALPHA_RISK_DEMO_ACCOUNT_SUB``）使用，让 agent 在对话里能真看到 RISK_REJECTED。

用法（端到端 5 步演示）：

    # 1. 注入 5 笔亏损 trade（默认 BTC/USDT@binance / SELL 平 long / -2.5%/笔）
    uv run python services/paper/scripts/demo_risk_seed.py --sub test-user

    # 2. 重启 paper service 时启用 demo mode
    export INALPHA_RISK_DEMO_ACCOUNT_SUB=test-user
    bash scripts/dev.sh   # paper service 启动 log 应出现"RiskGuard demo mode"

    # 3. 跟 agent 用同 sub 对话（默认 JWT sub=test-user）：
    #    "下一笔 0.001 BTC 多单" → trade.execute_plan 应返 409 RISK_REJECTED
    #    rule_name=StoplossGuardRule，agent 应清楚转述给用户

    # 4. 验证 risk_locks 表多一行
    psql ... -c "SELECT * FROM risk_locks WHERE active=TRUE;"

    # 5. 清理
    uv run python services/paper/scripts/demo_risk_seed.py --sub test-user --cleanup

约束：

- 不动 paper service 现有数据（只往 closed_trades 表 INSERT 合成行，--cleanup 删自己写的）
- 合成 trade 的 ``exit_reason`` 全是 ``'stop_loss'``（StoplossGuardRule 默认看这个值）
- 时间窗口：close_ts 均匀分布在过去 30 分钟内（落 StoplossGuardRule.lookback_min=60 窗口内）
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID

from inalpha_shared.db import close_pool, get_conn, init_pool

from inalpha_paper.account_id import account_id_from_sub
from inalpha_paper.storage import closed_trades as ct_store

logger = logging.getLogger(__name__)

_DEMO_TAG = "demo-risk-seed"
"""所有合成 trade 的 ``open_order_id`` 都写这个值，便于 --cleanup 精确删。"""


async def _seed(
    account_id: UUID,
    *,
    venue: str,
    symbol: str,
    count: int,
    loss_pct_per_trade: float,
    quantity: float,
    base_price: float,
) -> list[int]:
    """注入 ``count`` 笔模拟亏损 trade。返回新写入的 ID 列表。"""
    now = datetime.now(UTC)
    ids: list[int] = []
    async with get_conn() as conn:
        for i in range(count):
            # 时间均匀分布在过去 30min 内（保证全部落 StoplossGuardRule.lookback=60min）
            close_offset_min = 30 - i * (28 / max(count - 1, 1))
            close_ts = now - timedelta(minutes=close_offset_min)
            open_ts = close_ts - timedelta(minutes=5)
            close_price = base_price * (1 + loss_pct_per_trade)
            close_profit_abs = (close_price - base_price) * quantity
            lock_id = await ct_store.insert_close(
                conn,
                account_id=account_id,
                venue=venue,
                symbol=symbol,
                side="long",
                open_ts=open_ts,
                close_ts=close_ts,
                open_price=base_price,
                close_price=close_price,
                quantity=quantity,
                close_profit_pct=loss_pct_per_trade,
                close_profit_abs=close_profit_abs,
                exit_reason="stop_loss",
                open_order_id=_DEMO_TAG,
                close_order_id=_DEMO_TAG,
            )
            await conn.commit()
            ids.append(lock_id)
    return ids


async def _cleanup(account_id: UUID) -> int:
    """删本 demo 注入的所有 trade。返回删除行数。"""
    async with get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM closed_trades "
                "WHERE account_id = %s AND open_order_id = %s",
                (str(account_id), _DEMO_TAG),
            )
            await conn.commit()
            return cur.rowcount


async def _amain(args: argparse.Namespace) -> int:
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://quant:devpass@localhost:5433/inalpha",
    )
    account_id = account_id_from_sub(args.sub)
    print(f"sub: {args.sub!r} → account_id: {account_id}")

    await init_pool(db_url)
    try:
        if args.cleanup:
            n = await _cleanup(account_id)
            print(f"deleted {n} demo closed_trades")
            return 0

        ids = await _seed(
            account_id,
            venue=args.venue,
            symbol=args.symbol,
            count=args.count,
            loss_pct_per_trade=args.loss_pct,
            quantity=args.quantity,
            base_price=args.base_price,
        )
        print(f"inserted {len(ids)} demo closed_trades, ids={ids}")
        print()
        print("──── 下一步 ────")
        print(f"  export INALPHA_RISK_DEMO_ACCOUNT_SUB={args.sub}")
        print("  bash scripts/dev.sh   # 重启 paper service")
        print(f"  # 用 sub={args.sub} 的 JWT 跟 agent 对话，下 {args.symbol} 单 → 期望 409 RISK_REJECTED")
        print(f"  # 清理：uv run python services/paper/scripts/demo_risk_seed.py --sub {args.sub} --cleanup")
    finally:
        await close_pool()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="注入合成 closed_trades 让 StoplossGuardRule 真触发 demo。"
    )
    parser.add_argument(
        "--sub",
        required=True,
        help="JWT subject 字符串（跟 agent 用的 token sub 对齐）。account_id 由它派生。",
    )
    parser.add_argument(
        "--venue",
        default="binance",
        help="venue（默认 binance）",
    )
    parser.add_argument(
        "--symbol",
        default="BTC/USDT",
        help="symbol（默认 BTC/USDT；DB 存的形式不含 @venue 后缀）",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        help="注入笔数（默认 5，跟 StoplossGuardRule.trade_limit=5 对齐 → 触发）",
    )
    parser.add_argument(
        "--loss-pct",
        type=float,
        default=-0.025,
        help="每笔亏损百分比（默认 -2.5%%，需要 < required_profit=0.0 才计为'止损'）",
    )
    parser.add_argument(
        "--quantity",
        type=float,
        default=0.01,
        help="每笔数量（默认 0.01 BTC）",
    )
    parser.add_argument(
        "--base-price",
        type=float,
        default=50_000.0,
        help="开仓基准价（默认 50000，用于算 close_price = base*(1+loss_pct)）",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="删除本脚本写入的所有 demo trade（按 open_order_id 标记精确删）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
