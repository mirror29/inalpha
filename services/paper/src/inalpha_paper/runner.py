"""把 ``BacktestRequest`` 翻译成 ``BacktestEngine`` 调用并组装响应。

把"拉数据 + 实例化策略 + 跑引擎 + 转报告"的所有粘合代码集中在这里，让 api 层薄。

D-8c 起：可选落库 —— 调用方传 ``conn`` 时把回测结果写 ``backtest_runs`` 表
并返回 run_id；不传 conn 时退化为旧的"in-memory only"行为（向后兼容）。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from inalpha_shared.errors import ValidationError
from psycopg import AsyncConnection

from .data_client import DataClient
from .engine.backtest import BacktestEngine
from .kernel.identifiers import InstrumentId
from .model.data import Bar
from .schemas import BacktestRequest, BacktestResponse, EquityPoint, PositionSnapshot
from .storage import backtest_runs as backtest_runs_store
from .strategies import get_strategy_class

logger = logging.getLogger(__name__)


async def run_backtest(
    req: BacktestRequest,
    data_client: DataClient,
    *,
    conn: AsyncConnection | None = None,
) -> BacktestResponse:
    """执行一次完整 backtest：拉 bars → 实例化 strategy → 跑 engine → 组装响应。

    Args:
        req: 回测请求体（含可选 ``research_id`` / ``strategy_hint`` 血缘）
        data_client: data-service 客户端
        conn: 可选 DB 连接；传入则落 ``backtest_runs`` 表并把 ``run_id`` 写入响应。
              落库失败不阻断回测返回（warning log）—— D-8b' 容错原则。
    """
    started_at = datetime.now(tz=UTC)
    # 1. 拉数据
    raw_bars = await data_client.get_bars(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        from_ts=req.from_ts,
        to_ts=req.to_ts,
    )

    instrument_id = InstrumentId(symbol=req.symbol, venue=req.venue)
    bars = [_bar_from_dict(b, instrument_id, req.timeframe) for b in raw_bars]
    if not bars:
        raise ValidationError(
            f"data-service returned 0 bars for {req.symbol}@{req.venue} "
            f"{req.timeframe} [{req.from_ts.isoformat()}, {req.to_ts.isoformat()}]; "
            f"backfill via data-service /backfill/bars first",
            code="NO_BARS_AVAILABLE",
            details={
                "venue": req.venue,
                "symbol": req.symbol,
                "timeframe": req.timeframe,
                "from_ts": req.from_ts.isoformat(),
                "to_ts": req.to_ts.isoformat(),
            },
        )

    # 2. 实例化 engine + strategy
    engine = BacktestEngine(initial_cash=req.initial_cash, fee_rate=req.fee_rate)
    strategy_cls = get_strategy_class(req.strategy_id)

    # strategy_cls 是 type[Strategy] 但具体子类构造签名不同（SMA cross 还要
    # instrument_id / timeframe / 策略参数）。MVP 不抽 strategy factory，直接 type:ignore。
    strategy = strategy_cls(  # type: ignore[call-arg]
        name=f"{req.strategy_id}-{instrument_id.symbol}",
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=instrument_id,
        timeframe=req.timeframe,
        **req.params,
    )
    engine.add_strategy(strategy)

    # 3. 跑回测
    report = engine.run(bars)

    # 4. 组装响应
    final_positions = [
        PositionSnapshot(
            instrument_id=str(inst),
            quantity=pos.quantity,
            avg_open_price=pos.avg_open_price,
            realized_pnl=pos.realized_pnl,
            generation=pos.generation,
        )
        for inst, pos in report.positions.items()
        if not pos.is_flat
    ]

    equity_points = [
        EquityPoint(
            ts=datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=UTC),
            equity=eq,
        )
        for ts_ns, eq in report.equity_curve
    ]

    finished_at = datetime.now(tz=UTC)

    # 5. 可选落库 + 计算 params_hash（即使不落库也算给响应用）
    params_hash = backtest_runs_store.compute_params_hash(req.strategy_id, req.params)
    run_id: UUID | None = None
    if conn is not None:
        run_id = await _persist_run(
            conn=conn,
            req=req,
            report=report,
            started_at=started_at,
            finished_at=finished_at,
        )

    return BacktestResponse(
        run_id=run_id,
        research_id=req.research_id,
        params_hash=params_hash,
        strategy_id=req.strategy_id,
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        initial_cash=report.initial_cash,
        final_equity=report.final_equity,
        total_return_pct=report.total_return_pct,
        num_trades=report.num_trades,
        total_fees=report.total_fees,
        num_bars_processed=report.num_bars_processed,
        period_start=report.period_start or req.from_ts,
        period_end=report.period_end or req.to_ts,
        sharpe=report.sharpe,
        sortino=report.sortino,
        max_drawdown_pct=report.max_drawdown_pct,
        win_rate=report.win_rate,
        equity_curve=equity_points,
        final_positions=final_positions,
    )


async def _persist_run(
    *,
    conn: AsyncConnection,
    req: BacktestRequest,
    report: Any,
    started_at: datetime,
    finished_at: datetime,
) -> UUID | None:
    """写一行 backtest_runs。失败 log warning 后返 None，不阻断回测响应。"""
    config = {
        "venue": req.venue,
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "from_ts": req.from_ts.isoformat(),
        "to_ts": req.to_ts.isoformat(),
        "initial_cash": req.initial_cash,
        "fee_rate": req.fee_rate,
        "params": req.params,
    }
    metrics = {
        "sharpe": report.sharpe,
        "sortino": report.sortino,
        "max_drawdown_pct": report.max_drawdown_pct,
        "win_rate": report.win_rate,
        "total_return_pct": report.total_return_pct,
        "num_trades": report.num_trades,
        "total_fees": report.total_fees,
        "num_bars_processed": report.num_bars_processed,
        "final_equity": report.final_equity,
    }
    try:
        async with conn.transaction():
            return await backtest_runs_store.insert_run(
                conn,
                strategy_code=req.strategy_id,
                config=config,
                metrics=metrics,
                research_id=req.research_id,
                strategy_hint=req.strategy_hint,
                started_at=started_at,
                finished_at=finished_at,
            )
    except Exception:
        logger.warning(
            "backtest_runs insert failed",
            exc_info=True,
            extra={
                "strategy_code": req.strategy_id,
                "research_id": str(req.research_id) if req.research_id else None,
            },
        )
        return None


def _datetime_to_ns(dt: datetime) -> int:
    """``datetime`` → 纳秒整数，**不走 float**。

    旧实现 ``int(dt.timestamp() * 1_000_000_000)`` 对 2026 年的时间戳精度不够：
    ts_ns ≈ 1.7e18 超 float64 mantissa，可能丢 ~100ns，导致 ``Portfolio.snapshot``
    用 ``==`` 比较 ts_ns 时误覆盖（D-8b' review 高风险 #5）。

    分两步：先取整秒，再补 microsecond × 1000，纯整数运算。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    # int(dt.timestamp()) 仍走 float，但秒数量级 < 2^53 安全
    secs = int(dt.timestamp())
    return secs * 1_000_000_000 + dt.microsecond * 1_000


def _bar_from_dict(d: dict[str, Any], instrument_id: InstrumentId, timeframe: str) -> Bar:
    """data-service ``BarResponse`` dict → 内核 ``Bar`` dataclass。"""
    # ts 字段 data-service 返 ISO datetime 字符串
    ts_str = d["ts"]
    if isinstance(ts_str, str):
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    else:
        dt = ts_str
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    ts_ns = _datetime_to_ns(dt)

    return Bar(
        instrument_id=instrument_id,
        timeframe=timeframe,
        open=float(d["open"]),
        high=float(d["high"]),
        low=float(d["low"]),
        close=float(d["close"]),
        volume=float(d["volume"]),
        ts_event=ts_ns,
        ts_init=ts_ns,
    )
