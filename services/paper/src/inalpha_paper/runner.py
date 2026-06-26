"""把 ``BacktestRequest`` 翻译成 ``BacktestEngine`` 调用并组装响应。

把"拉数据 + 实例化策略 + 跑引擎 + 转报告"的所有粘合代码集中在这里，让 api 层薄。

D-8c 起：可选落库 —— 调用方传 ``conn`` 时把回测结果写 ``backtest_runs`` 表
并返回 run_id；不传 conn 时退化为旧的"in-memory only"行为（向后兼容）。

Swarm S1（ADR-0025）起：CPU 重活（engine + strategy + ``engine.run(bars)``）抽到
``run_engine_in_subprocess`` 顶层函数，async ``run_backtest`` 通过 ``ProcessPoolExecutor``
``loop.run_in_executor`` 提交。HTTP I/O / DB 写仍在 main 协程里。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from inalpha_shared.errors import NotFoundError, ValidationError
from psycopg import AsyncConnection

from .config import get_paper_settings
from .data_client import DataClient
from .engine.backtest import BacktestEngine, CVReport, run_cv_backtest
from .engine.cv import (
    CombinatorialPurgedCV,
    InsufficientDataError,
    PurgedKFold,
    WalkForward,
)
from .engine.metrics import (
    bar_returns,
    max_drawdown_pct,
    periods_per_year,
    sharpe_ratio,
)
from .engine.pool import get_pool
from .engine.robustness import bootstrap_sharpe_ci
from .kernel.clock import datetime_to_ns
from .kernel.identifiers import InstrumentId
from .model.data import Bar
from .schemas import (
    BacktestRequest,
    BacktestResponse,
    BaselineSnapshot,
    CVBacktestRequest,
    CVBacktestResponse,
    EquityPoint,
    PositionSnapshot,
    SharpeCI,
    ValidationBlock,
    ValidationSegment,
)
from .storage import backtest_runs as backtest_runs_store
from .storage import backtest_trades as backtest_trades_store
from .storage import strategy_candidates as candidates_store
from .strategies import BASELINE_BUY_AND_HOLD, get_strategy_class
from .strategy.base import Strategy
from .strategy_authoring import (
    FitnessInputs,
    audit_strategy_code,
    calmar_from_report,
    compose_fitness,
    load_strategy_class,
    verify_strategy_contract,
)

if TYPE_CHECKING:
    from concurrent.futures import ProcessPoolExecutor

    from .engine.report import BacktestReport

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
    # 1. 拉数据 —— DataClient.get_bars 默认 fresh=True：先 POST /backfill/bars
    # 把 to_ts 之前最新 K 线补齐，再 GET /bars。新 symbol / 新窗口在这一步自愈。
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
            f"{req.timeframe} [{req.from_ts.isoformat()}, {req.to_ts.isoformat()}] "
            f"even after fresh-backfill; symbol may not exist on {req.venue} "
            f"or the time range is invalid (future / pre-listing)",
            code="NO_BARS_AVAILABLE",
            details={
                "venue": req.venue,
                "symbol": req.symbol,
                "timeframe": req.timeframe,
                "from_ts": req.from_ts.isoformat(),
                "to_ts": req.to_ts.isoformat(),
            },
        )

    # D-9 · candidate 路径：从 strategy_candidates 表读源码 + 二次审计（defense in depth）
    candidate_code: str | None = None
    if req.candidate_id is not None:
        if conn is None:
            raise ValidationError(
                "candidate_id path requires database connection",
                code="CANDIDATE_NO_DB",
            )
        candidate_row = await candidates_store.get_candidate(conn, req.candidate_id)
        if candidate_row is None:
            raise NotFoundError(
                f"candidate {req.candidate_id} not found",
                code="CANDIDATE_NOT_FOUND",
            )
        candidate_code = candidate_row["code"]
        # 二次 AST 审计 —— 即使入库时已审过，跑回测前再审一次
        reaudit = audit_strategy_code(candidate_code)
        if not reaudit.ok:
            raise ValidationError(
                f"candidate {req.candidate_id} failed re-audit: {reaudit.reason()}",
                code="CANDIDATE_REAUDIT_FAILED",
            )

    # 用作落库 / 响应的 strategy_code（"candidate:<uuid>" 与内置 ID 同字段区分）
    effective_strategy_code = (
        req.strategy_id
        if req.strategy_id is not None
        else f"candidate:{req.candidate_id}"
    )

    # 2-3. 实例化 engine + strategy + 跑回测（CPU 重活，丢 ProcessPool）
    # 优先级：pool 已起（生产）→ 走 pool；未起（旧测试 / 同步入口）→ 同进程跑兜底
    # D-9 candidate 路径：同时并发跑一次 buy_and_hold baseline 做 alpha 对照
    # （pool 已起时真并行；未起时退化为顺序但语义不变）
    candidate_task = _run_engine(
        bars=bars,
        instrument_id=instrument_id,
        timeframe=req.timeframe,
        strategy_id=req.strategy_id,
        candidate_code=candidate_code,
        params=req.params,
        initial_cash=req.initial_cash,
        fee_rate=req.fee_rate,
        # perp 透传:做空策略须在 perp 回测才能真做空(spot 下裸空被守门拒=0 成交)。
        # baseline(下方 buy_and_hold)保持 spot,作 alpha 对照锚点。
        trading_mode=req.trading_mode,
        leverage=req.leverage,
        funding_rate=req.funding_rate,
    )
    try:
        if req.candidate_id is not None:
            # baseline = "first bar all-in 持有到收盘"。BuyAndHoldStrategy 在
            # bars[0] 发单 → bars[1] 才撮合（避免 lookahead）。撮合层 cash 守门
            # 用的是 bars[1].open，所以预算 qty 也用 bars[1].open；fee_rate +
            # 0.5% 容忍连续两根 bar 间的价格 jitter（一般 < 1%）。
            fill_open = bars[1].open if len(bars) > 1 else bars[0].open
            if fill_open <= 0:
                raise ValidationError(
                    f"bar open price must be positive, got {fill_open} "
                    f"for {req.symbol}@{req.venue}; data may be corrupt",
                    code="INVALID_BAR_PRICE",
                )
            baseline_qty = req.initial_cash / fill_open / (1.0 + req.fee_rate + 0.005)
            baseline_task = _run_engine(
                bars=bars,
                instrument_id=instrument_id,
                timeframe=req.timeframe,
                strategy_id=BASELINE_BUY_AND_HOLD,
                candidate_code=None,
                params={"trade_size": baseline_qty},
                initial_cash=req.initial_cash,
                fee_rate=req.fee_rate,
            )
            report, baseline_report = await asyncio.gather(candidate_task, baseline_task)
        else:
            report = await candidate_task
            baseline_report = None
    except (AttributeError, TypeError, ValueError, KeyError, IndexError, ZeroDivisionError) as exc:
        # D-9 · candidate 路径策略源码运行时错（字段名 / 类型 / 数学）→ 翻 422 让 agent
        # 改源码。如果是内置策略走到这里属于服务端 bug，原样抛出。
        if req.candidate_id is None:
            raise
        raise ValidationError(
            f"candidate strategy runtime error: {type(exc).__name__}: {exc}. "
            "Check field names against paper.author_strategy tool description's "
            "field cheat-sheet (e.g. PositionEvent uses 'avg_open_price' not 'avg_price'; "
            "OrderFilled uses 'fill_quantity'/'fill_price' not 'filled_quantity'/'avg_fill_price'). "
            "Re-author the candidate with corrected source.",
            code="STRATEGY_RUNTIME_ERROR",
            details={
                "candidate_id": str(req.candidate_id),
                "exception_type": type(exc).__name__,
            },
        ) from exc

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

    # 5. 计算 fitness（D-9 · ADR-0020 E1：多目标合成，不允许裸 Sharpe 排序候选）
    bars_per_year = float(periods_per_year(req.timeframe))
    fitness_value = _fitness_from_report(report, bars_per_year=bars_per_year)
    # calmar 指标落库一律取 report.calmar —— 不再走 calmar_from_report 第二条
    # 计算路径,否则 fitness 侧公式一变,candidates 与 backtest_runs 两表的
    # calmar 会静默分叉。

    # 5a'. D-12：holdout 时间切分验证（单次运行按曲线切段，不二次跑引擎）。
    # baseline 不切段——alpha 对照仍看全窗。
    validation: ValidationBlock | None = None
    if req.validation_split > 0:
        validation = _validation_from_report(
            report, split=req.validation_split, bars_per_year=bars_per_year
        )

    # 5b. D-9 candidate 路径：组装 baseline 对照（buy_and_hold 同 symbol/timeframe/period）
    baseline_snapshot: BaselineSnapshot | None = None
    if baseline_report is not None:
        baseline_fitness = _fitness_from_report(
            baseline_report, bars_per_year=bars_per_year
        )
        baseline_snapshot = BaselineSnapshot(
            strategy_id=BASELINE_BUY_AND_HOLD,
            fitness=baseline_fitness,
            sharpe=baseline_report.sharpe,
            max_drawdown_pct=baseline_report.max_drawdown_pct,
            total_return_pct=baseline_report.total_return_pct,
            num_trades=baseline_report.num_trades,
            blew_up=baseline_report.blew_up,
        )

    # 6. 可选落库 + 计算 params_hash（即使不落库也算给响应用）
    params_hash = backtest_runs_store.compute_params_hash(
        effective_strategy_code, req.params
    )
    run_id: UUID | None = None
    if conn is not None:
        # run 行 + 逐笔成交同一事务写入(只写主候选/内置策略的 fills,**不写 baseline**):
        # 拆两事务时 trades 失败会留下"有记录但成交永远空白"的孤儿 run。
        run_id = await _persist_run(
            conn=conn,
            req=req,
            strategy_code=effective_strategy_code,
            fitness=fitness_value,
            report=report,
            started_at=started_at,
            finished_at=finished_at,
            validation=validation,
        )
        # 6a. candidate 路径：回写 candidates 表（最近一次 metrics / fitness）
        if req.candidate_id is not None:
            try:
                await candidates_store.update_after_backtest(
                    conn,
                    req.candidate_id,
                    metrics={
                        "sharpe": report.sharpe,
                        "sortino": report.sortino,
                        "max_drawdown_pct": report.max_drawdown_pct,
                        "win_rate": report.win_rate,
                        "total_return_pct": report.total_return_pct,
                        "num_trades": report.num_trades,
                        "num_bars_processed": report.num_bars_processed,
                        "initial_cash": report.initial_cash,
                        "final_equity": report.final_equity,
                        "calmar": report.calmar,
                        # 专业级扩展指标（与 _persist_run 的 metrics 同源,可 None）
                        "annualized_return_pct": report.annualized_return_pct,
                        "annualized_volatility_pct": report.annualized_volatility_pct,
                        "profit_factor": report.profit_factor,
                        "payoff_ratio": report.payoff_ratio,
                        "expectancy": report.expectancy,
                        "best_trade_pnl": report.best_trade_pnl,
                        "worst_trade_pnl": report.worst_trade_pnl,
                        "max_consecutive_wins": report.max_consecutive_wins,
                        "max_consecutive_losses": report.max_consecutive_losses,
                        "max_drawdown_duration_bars": report.max_drawdown_duration_bars,
                        "exposure_pct": report.exposure_pct,
                        # D-12：holdout 验证摘要随 metrics 落 candidate（promote 门槛读）
                        "validation": validation.model_dump(mode="json")
                        if validation is not None
                        else None,
                    },
                    fitness=fitness_value,
                    backtest_run_id=run_id,
                )
            except Exception:
                logger.warning(
                    "candidate update_after_backtest failed",
                    exc_info=True,
                    extra={"candidate_id": str(req.candidate_id)},
                )

    return BacktestResponse(
        run_id=run_id,
        research_id=req.research_id,
        params_hash=params_hash,
        strategy_id=effective_strategy_code,
        candidate_id=req.candidate_id,
        fitness=fitness_value,
        baseline=baseline_snapshot,
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
        blew_up=report.blew_up,
        health_warnings=list(report.health_warnings),
        protective_exits=report.protective_exits,
        sharpe_ci=(
            SharpeCI(
                lower=report.sharpe_ci_lower,
                upper=report.sharpe_ci_upper,
                includes_zero=report.sharpe_ci_includes_zero,
            )
            # 三字段全判：SharpeCI.lower/upper 是 required float，只判 includes_zero
            # 不能在类型层收窄它俩；未来若破坏 from_portfolio 的原子赋值会 Pydantic 500（CR）
            if (
                report.sharpe_ci_lower is not None
                and report.sharpe_ci_upper is not None
                and report.sharpe_ci_includes_zero is not None
            )
            else None
        ),
        final_positions=final_positions,
        validation=validation,
    )


def _validation_from_report(
    report: Any,
    *,
    split: float,
    bars_per_year: float,
) -> ValidationBlock | None:
    """单次回测结果按时间切 train/holdout 两段算指标（D-12 · 纯函数）。

    切的是 ``report.equity_curve``（全量，未降采样）——不二次跑引擎、不重拉数据、
    不改变 run 记录语义。holdout 段衰减比 = holdout_sharpe / train_sharpe，是
    "这套参数是不是只在前半窗有效"的最便宜信号。

    **不是盲 OOS**：调用方（agent）看得到 holdout 指标，反复对着它调参会间接
    过拟合；纪律约束在 orchestrator prompt（调参看 train，holdout 只作裁判）。

    Returns:
        曲线 < 10 点或任一段 < 2 点（切不出有意义的段）时返 ``None``。
    """
    curve: list[tuple[int, float]] = report.equity_curve
    if len(curve) < 10:
        return None
    split_idx = int(len(curve) * split)
    if split_idx < 2 or len(curve) - split_idx < 2:
        return None

    cut_ts_ns = curve[split_idx][0]
    values = [eq for _ts, eq in curve]
    train_vals = values[:split_idx]
    # holdout 段带上切点前一根作收益率基准（首根 holdout return 需要前值）
    holdout_vals = values[split_idx - 1 :]

    fills = list(getattr(report, "fills", []) or [])
    train_fills = sum(1 for f in fills if f.ts_ns < cut_ts_ns)
    holdout_fills = len(fills) - train_fills

    def _segment(
        vals: list[float], num_trades: int, *, bar_count: int | None = None
    ) -> ValidationSegment:
        rets = bar_returns(vals)
        return ValidationSegment(
            sharpe=sharpe_ratio(rets, int(bars_per_year)),
            total_return_pct=(vals[-1] / vals[0] - 1.0) * 100.0 if vals[0] > 0 else 0.0,
            max_drawdown_pct=max_drawdown_pct(vals),
            num_trades=num_trades,
            num_bars=bar_count if bar_count is not None else len(vals),
        )

    train_seg = _segment(train_vals, train_fills)
    # holdout_vals 多带切点前一根作收益率基准 → num_bars 报真实 holdout 段长（len-1），
    # 否则 insufficient_sample 的 <30 判据在真实 29 根时被 +1 顶到 30 漏报（CR #86）。
    holdout_seg = _segment(
        holdout_vals, holdout_fills, bar_count=len(holdout_vals) - 1
    )

    flags: list[str] = []
    # holdout 段自身要有足够样本：用 holdout_seg.num_trades 而非全窗口 report.num_trades
    # ——策略可能 train 段交易密集、holdout 段几乎不动，全窗口 ≥5 却 holdout 只 1-2 笔，
    # 此时 holdout Sharpe / decay_ratio 不可信，必须 flag（CR #86 major）。
    if (
        holdout_seg.num_bars < 30
        or holdout_seg.num_trades < 2
        or report.num_trades < 5
    ):
        flags.append("insufficient_sample")

    decay_ratio: float | None = None
    if train_seg.sharpe is None or holdout_seg.sharpe is None:
        flags.append("sharpe_undefined")
    elif train_seg.sharpe <= 0:
        # train 段本身就不赚：衰减比无意义（负除负会假装"没衰减"）
        flags.append("train_sharpe_nonpositive")
    else:
        decay_ratio = holdout_seg.sharpe / train_seg.sharpe

    ci_includes_zero: bool | None = None
    holdout_rets = bar_returns(holdout_vals)
    if len(holdout_rets) >= 30:
        try:
            ci = bootstrap_sharpe_ci(holdout_rets, n_samples=1000)
            ci_includes_zero = ci.ci_includes_zero
        except Exception:
            logger.warning("bootstrap_sharpe_ci failed", exc_info=True)

    return ValidationBlock(
        split_ratio=split,
        train=train_seg,
        holdout=holdout_seg,
        decay_ratio=decay_ratio,
        holdout_sharpe_ci_includes_zero=ci_includes_zero,
        flags=flags,
    )


async def _persist_run(
    *,
    conn: AsyncConnection,
    req: BacktestRequest,
    strategy_code: str,
    fitness: float,
    report: Any,
    started_at: datetime,
    finished_at: datetime,
    validation: ValidationBlock | None = None,
) -> UUID | None:
    """写一行 backtest_runs + 逐笔成交(同一事务)。失败 log warning 后返 None，不阻断回测响应。

    run 行与 ``backtest_trades`` 必须同事务：拆开写时 trades 失败会留下
    "run_id 已暴露给调用方、UI 成交表却永远空白"的孤儿行。
    """
    config = {
        "venue": req.venue,
        "symbol": req.symbol,
        "timeframe": req.timeframe,
        "from_ts": req.from_ts.isoformat(),
        "to_ts": req.to_ts.isoformat(),
        "initial_cash": req.initial_cash,
        "fee_rate": req.fee_rate,
        "params": req.params,
        "candidate_id": str(req.candidate_id) if req.candidate_id else None,
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
        "initial_cash": report.initial_cash,
        "final_equity": report.final_equity,
        "fitness": fitness,
        # 专业级扩展指标（D-11+,engine/metrics.py;可 None）
        "annualized_return_pct": report.annualized_return_pct,
        "annualized_volatility_pct": report.annualized_volatility_pct,
        "calmar": report.calmar,
        "profit_factor": report.profit_factor,
        "payoff_ratio": report.payoff_ratio,
        "expectancy": report.expectancy,
        "best_trade_pnl": report.best_trade_pnl,
        "worst_trade_pnl": report.worst_trade_pnl,
        "max_consecutive_wins": report.max_consecutive_wins,
        "max_consecutive_losses": report.max_consecutive_losses,
        "max_drawdown_duration_bars": report.max_drawdown_duration_bars,
        "exposure_pct": report.exposure_pct,
        # D-12：holdout 验证摘要（与响应同源，None = 曲线太短/显式关闭）
        "validation": validation.model_dump(mode="json") if validation is not None else None,
    }
    try:
        async with conn.transaction():
            run_id = await backtest_runs_store.insert_run(
                conn,
                strategy_code=strategy_code,
                config=config,
                metrics=metrics,
                research_id=req.research_id,
                strategy_hint=req.strategy_hint,
                started_at=started_at,
                finished_at=finished_at,
            )
            if report.fills:
                await backtest_trades_store.insert_fills(conn, run_id, report.fills)
            return run_id
    except Exception:
        logger.warning(
            "backtest_runs insert failed",
            exc_info=True,
            extra={
                "strategy_code": strategy_code,
                "research_id": str(req.research_id) if req.research_id else None,
            },
        )
        return None


def _fitness_from_report(report: Any, *, bars_per_year: float) -> float:
    """从 ``BacktestReport`` 算多目标 fitness（D-9 · ADR-0020 §适应度函数）。

    抽出公用函数避免主路径 / baseline 路径重复算 calmar + compose_fitness。
    """
    calmar = calmar_from_report(
        total_return_pct=report.total_return_pct,
        max_drawdown_pct=report.max_drawdown_pct,
        num_bars_processed=report.num_bars_processed,
        bars_per_year=bars_per_year,
    )
    return compose_fitness(
        FitnessInputs(
            sharpe=report.sharpe,
            calmar=calmar,
            max_drawdown_pct=report.max_drawdown_pct,
            num_trades=report.num_trades,
            num_bars_processed=report.num_bars_processed,
        )
    )


async def run_cv(
    req: CVBacktestRequest,
    data_client: DataClient,
    *,
    conn: AsyncConnection | None = None,
) -> CVBacktestResponse:
    """跑多路径时序交叉验证回测（ADR-0028）：拉数据 → 造策略工厂 → 切分 → 聚合分布。

    与 ``run_backtest`` 平行的入口；CV 评估稳健性，不落 backtest_runs（多路径无单一 run）。
    cpcv 在 bar 不足（< 200 或 < 2×n_folds）时**自动回落 walk_forward**（CLAUDE.md §3.1
    末段含最新 bar 由 splitter 保证）。
    """
    # 拉数据——默认 fresh=True：先 backfill 再读，确保 to_ts 前数据完整（与 run_backtest 同
    # 口径，CLAUDE.md §3.1）。CV 多为历史窗口但仍主动选 fresh，避免新 symbol / 新窗口
    # 缺数据；如需纯历史不回填须显式说明再改 fresh=False（别误当遗忘默认）。
    raw_bars = await data_client.get_bars(
        venue=req.venue,
        symbol=req.symbol,
        timeframe=req.timeframe,
        from_ts=req.from_ts,
        to_ts=req.to_ts,
    )
    instrument_id = InstrumentId(symbol=req.symbol, venue=req.venue)
    bars = [_bar_from_dict(b, instrument_id, req.timeframe) for b in raw_bars]
    if len(bars) < 2:
        raise ValidationError(
            f"CV needs >= 2 bars, got {len(bars)} for {req.symbol}@{req.venue}",
            code="NO_BARS_AVAILABLE",
        )

    # 策略源解析（内置 id 或 LLM 候选源码字符串）。候选在 main 二次过 ast_audit；
    # 策略**类的构建放到子进程 worker**（类对象不可 pickle，且 CPU 重活不应在事件循环里）。
    candidate_code: str | None = None
    if req.candidate_id is not None:
        if conn is None:
            raise ValidationError(
                "candidate_id path requires database connection", code="CANDIDATE_NO_DB"
            )
        row = await candidates_store.get_candidate(conn, req.candidate_id)
        if row is None:
            raise NotFoundError(
                f"candidate {req.candidate_id} not found", code="CANDIDATE_NOT_FOUND"
            )
        reaudit = audit_strategy_code(row["code"])
        if not reaudit.ok:
            raise ValidationError(
                f"candidate {req.candidate_id} failed re-audit: {reaudit.reason()}",
                code="CANDIDATE_REAUDIT_FAILED",
            )
        candidate_code = row["code"]

    splitter: CombinatorialPurgedCV | PurgedKFold | WalkForward
    if req.splitter == "cpcv":
        splitter = CombinatorialPurgedCV(
            req.n_folds, req.n_test_folds, embargo_pct=req.embargo_pct
        )
    elif req.splitter == "purged_kfold":
        splitter = PurgedKFold(req.n_folds, embargo_pct=req.embargo_pct)
    else:
        splitter = WalkForward(req.wf_test_size, req.wf_train_size)

    async def _offload(spl: CombinatorialPurgedCV | PurgedKFold | WalkForward) -> CVReport:
        """把 N 路 CV 整体甩进 ProcessPool（#99 CR：别在事件循环里同步跑 30 条引擎）。

        pool 未起（旧测试 / 同步入口）→ 同进程兜底跑（语义一致）。
        """
        from functools import partial

        fn = partial(
            run_cv_in_subprocess,
            bars=bars,
            instrument_id=instrument_id,
            timeframe=req.timeframe,
            strategy_id=req.strategy_id,
            candidate_code=candidate_code,
            params=req.params,
            initial_cash=req.initial_cash,
            fee_rate=req.fee_rate,
            splitter=spl,
            trading_mode=req.trading_mode,
            leverage=req.leverage,
            funding_rate=req.funding_rate,
        )
        try:
            pool = get_pool()
        except RuntimeError:
            pool = None
        if pool is None:
            # pool 不可用（容器禁 fork / 启动失败 / 旧测试同步入口）→ 同进程兜底跑。
            # 生产环境这是从"offload"降级回"阻塞事件循环",运维需可感知（#99 CR）。
            logger.warning(
                "cv_offload_pool_unavailable_running_sync",
                extra={"n_bars": len(bars), "splitter": type(spl).__name__},
            )
            return fn()
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(pool, fn)

    splitter_used = req.splitter
    note: str | None = None
    try:
        report = await _offload(splitter)
    except InsufficientDataError as exc:
        if req.splitter != "cpcv":
            raise ValidationError(
                f"CV splitter data insufficient: {exc}", code="CV_INSUFFICIENT_DATA"
            ) from exc
        # cpcv 数据不足 → 回落 walk_forward（ADR-0028 D1）
        splitter_used = "walk_forward"
        note = f"cpcv 数据不足（{len(bars)} bars），已回落 walk_forward：{exc}"
        try:
            report = await _offload(WalkForward(req.wf_test_size, req.wf_train_size))
        except InsufficientDataError as exc2:
            raise ValidationError(
                f"CV data insufficient even for walk_forward: {exc2}",
                code="CV_INSUFFICIENT_DATA",
            ) from exc2

    return CVBacktestResponse(
        symbol=req.symbol,
        timeframe=req.timeframe,
        n_bars=len(bars),
        splitter_used=splitter_used,
        n_paths=report.n_paths,
        n_splits=report.n_splits,
        sharpe_per_path=report.sharpe_per_path,
        max_dd_per_path=report.max_dd_per_path,
        sharpe_p5=report.sharpe_p5,
        sharpe_p50=report.sharpe_p50,
        sharpe_p95=report.sharpe_p95,
        sharpe_mean=report.sharpe_mean,
        dsr=report.dsr,
        dsr_p_value=report.dsr_p_value,
        note=note,
    )


def run_cv_in_subprocess(
    *,
    bars: list[Bar],
    instrument_id: InstrumentId,
    timeframe: str,
    strategy_id: str | None,
    candidate_code: str | None,
    params: dict[str, Any],
    initial_cash: float,
    fee_rate: float,
    splitter: CombinatorialPurgedCV | PurgedKFold | WalkForward,
    trading_mode: str = "spot",
    leverage: int = 1,
    funding_rate: float = 0.0,
) -> CVReport:
    """**Top-level 可 pickle 函数**：在子进程里解析策略类 + 跑多路 CV，返 ``CVReport``。

    与 ``run_engine_in_subprocess`` 同源（ADR-0025）：CPU 重活留子进程、main 协程不阻塞
    （#99 CR）。``InsufficientDataError`` 经 future 原样传回 main 决定回落。candidate 路径的
    AST 审计已在 main 做过，这里只 load。
    """
    if candidate_code is not None:
        strategy_cls = load_strategy_class(candidate_code)
        strategy_name = f"candidate-{instrument_id.symbol}"
    elif strategy_id is not None:
        strategy_cls = get_strategy_class(strategy_id)
        strategy_name = f"{strategy_id}-{instrument_id.symbol}"
    else:
        raise ValidationError(
            "internal: neither strategy_id nor candidate_code provided",
            code="STRATEGY_MISSING",
        )

    def build_strategy(engine: BacktestEngine) -> Strategy:
        kwargs: dict[str, Any] = dict(params)
        sig = inspect.signature(strategy_cls.__init__)
        if "initial_cash" in sig.parameters and "initial_cash" not in kwargs:
            kwargs["initial_cash"] = initial_cash
        if "position_pct" in sig.parameters and "position_pct" not in kwargs:
            kwargs["position_pct"] = 1.0
        return strategy_cls(  # type: ignore[call-arg]
            name=strategy_name,
            clock=engine.clock,
            msgbus=engine.msgbus,
            instrument_id=instrument_id,
            timeframe=timeframe,
            **kwargs,
        )

    return run_cv_backtest(
        build_strategy=build_strategy,
        bars=bars,
        splitter=splitter,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        trading_mode=trading_mode,
        leverage=leverage,
        funding_rate=funding_rate,
    )


async def _run_engine(
    *,
    bars: list[Bar],
    instrument_id: InstrumentId,
    timeframe: str,
    strategy_id: str | None,
    params: dict[str, Any],
    initial_cash: float,
    fee_rate: float,
    candidate_code: str | None = None,
    trading_mode: str = "spot",
    leverage: int = 1,
    funding_rate: float = 0.0,
) -> BacktestReport:
    """调度 engine 执行：pool 已起则丢 ProcessPool，未起则同进程跑兜底。

    pool 未起的情况只剩老单测路径（没走 lifespan）；生产 / 集成测试都该走 pool。

    D-9 起：``strategy_id`` 与 ``candidate_code`` 二选一（调用方保证）。
    """
    try:
        pool: ProcessPoolExecutor | None = get_pool()
    except RuntimeError:
        pool = None

    # ADR-0052：从 Settings 解析框架级持仓保护止损阈值（main 进程读，传给子进程；
    # 回测与 live 共用同一阈值，保证行为一致）。
    sl, tp, ts, ch_mult, ch_period = _protective_thresholds()

    if pool is None:
        # 兜底：同进程跑（不真正 CPU 并行，但函数语义一致）
        return run_engine_in_subprocess(
            bars=bars,
            instrument_id=instrument_id,
            timeframe=timeframe,
            strategy_id=strategy_id,
            candidate_code=candidate_code,
            params=params,
            initial_cash=initial_cash,
            fee_rate=fee_rate,
            protective_stop_loss_pct=sl,
            protective_take_profit_pct=tp,
            protective_trailing_stop_pct=ts,
            protective_chandelier_atr_mult=ch_mult,
            protective_chandelier_atr_period=ch_period,
            trading_mode=trading_mode,
            leverage=leverage,
            funding_rate=funding_rate,
        )

    loop = asyncio.get_running_loop()
    # run_in_executor 不支持 kwargs，包一层 lambda（不能 partial — kwargs 走不到 args）
    # Python lambda 默认参数延迟求值的问题这里不存在（一次性 closure）
    fn = _make_pool_call(
        bars=bars,
        instrument_id=instrument_id,
        timeframe=timeframe,
        strategy_id=strategy_id,
        candidate_code=candidate_code,
        params=params,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        protective_stop_loss_pct=sl,
        protective_take_profit_pct=tp,
        protective_trailing_stop_pct=ts,
        protective_chandelier_atr_mult=ch_mult,
        protective_chandelier_atr_period=ch_period,
        trading_mode=trading_mode,
        leverage=leverage,
        funding_rate=funding_rate,
    )
    return await loop.run_in_executor(pool, fn)


def _protective_thresholds() -> tuple[
    float | None, float | None, float | None, float | None, int
]:
    """从 Settings 读 ADR-0052 框架级持仓保护止损阈值
    ``(stop_loss, take_profit, trailing, chandelier_atr_mult, chandelier_atr_period)``。"""
    s = get_paper_settings()
    return (
        s.protective_stop_loss_pct,
        s.protective_take_profit_pct,
        s.protective_trailing_stop_pct,
        s.protective_chandelier_atr_mult,
        s.protective_chandelier_atr_period,
    )


def _make_pool_call(
    *,
    bars: list[Bar],
    instrument_id: InstrumentId,
    timeframe: str,
    strategy_id: str | None,
    params: dict[str, Any],
    initial_cash: float,
    fee_rate: float,
    candidate_code: str | None = None,
    protective_stop_loss_pct: float | None = None,
    protective_take_profit_pct: float | None = None,
    protective_trailing_stop_pct: float | None = None,
    protective_chandelier_atr_mult: float | None = None,
    protective_chandelier_atr_period: int = 22,
    trading_mode: str = "spot",
    leverage: int = 1,
    funding_rate: float = 0.0,
) -> Any:
    """生成一个无参 callable，丢给 ``run_in_executor``。

    需要这层间接因为 ``ProcessPoolExecutor.submit`` 接 ``(*args)`` 不接 kwargs，
    用 functools.partial 包 kwargs 也行，但闭包写法更显式。
    """
    from functools import partial

    return partial(
        run_engine_in_subprocess,
        bars=bars,
        instrument_id=instrument_id,
        timeframe=timeframe,
        strategy_id=strategy_id,
        candidate_code=candidate_code,
        params=params,
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        protective_stop_loss_pct=protective_stop_loss_pct,
        protective_take_profit_pct=protective_take_profit_pct,
        protective_trailing_stop_pct=protective_trailing_stop_pct,
        protective_chandelier_atr_mult=protective_chandelier_atr_mult,
        protective_chandelier_atr_period=protective_chandelier_atr_period,
        trading_mode=trading_mode,
        leverage=leverage,
        funding_rate=funding_rate,
    )


def run_engine_in_subprocess(
    *,
    bars: list[Bar],
    instrument_id: InstrumentId,
    timeframe: str,
    strategy_id: str | None,
    params: dict[str, Any],
    initial_cash: float,
    fee_rate: float,
    candidate_code: str | None = None,
    protective_stop_loss_pct: float | None = None,
    protective_take_profit_pct: float | None = None,
    protective_trailing_stop_pct: float | None = None,
    protective_chandelier_atr_mult: float | None = None,
    protective_chandelier_atr_period: int = 22,
    trading_mode: str = "spot",
    leverage: int = 1,
    funding_rate: float = 0.0,
) -> BacktestReport:
    """**Top-level 函数 = 可 pickle**：实例化 engine + strategy + 跑 bars，返 ``BacktestReport``。

    在子进程里跑（ADR-0025 §D1）：

    - 不调任何 async / httpx / DB —— 全部 IO 留在 main 协程
    - 只接受 picklable 输入（dataclass + 原生 dict / list）
    - raise 直接通过 future 传回 main，main 翻成 HTTP 5xx 结构化错误
    - rlimit / CPU 超时由 ``engine.pool._worker_init`` 在 worker 启动时设置

    放在 runner.py 而不是 pool.py：保持调用现场的 import 上下文（``BacktestEngine`` 之类
    模块在 worker 第一次 fork 后由 pickle 反序列化时按需 import）。

    D-9 · candidate 分支：
    - ``candidate_code`` 非空 → ``dynamic_loader`` + ``verify_strategy_contract`` 在子进程内跑
      （main 进程已审过；这里只走 load + contract，避免再次 AST 浪费 CPU）
    - 否则走内置 ``get_strategy_class(strategy_id)``
    """
    # ADR-0052：框架级持仓保护止损阈值由 main 进程从 Settings 解析后传入（子进程不读
    # Settings 单例，保持 picklable + 纯）。三阈值全 None → BacktestEngine 不建 guard。
    engine = BacktestEngine(
        initial_cash=initial_cash,
        fee_rate=fee_rate,
        protective_stop_loss_pct=protective_stop_loss_pct,
        protective_take_profit_pct=protective_take_profit_pct,
        protective_trailing_stop_pct=protective_trailing_stop_pct,
        protective_chandelier_atr_mult=protective_chandelier_atr_mult,
        protective_chandelier_atr_period=protective_chandelier_atr_period,
        trading_mode=trading_mode,
        leverage=leverage,
        funding_rate=funding_rate,
    )

    if candidate_code is not None:
        strategy_cls = load_strategy_class(candidate_code)
        verify_strategy_contract(strategy_cls)
        strategy_name = f"{strategy_cls.__name__}-{instrument_id.symbol}"
    else:
        if strategy_id is None:
            raise ValidationError(
                "internal: neither strategy_id nor candidate_code provided",
                code="STRATEGY_MISSING",
            )
        strategy_cls = get_strategy_class(strategy_id)
        strategy_name = f"{strategy_id}-{instrument_id.symbol}"

    # strategy 子类构造签名不一（SMA cross 要 lookback、mean_rev 要 threshold 等）；
    # MVP 不抽 strategy factory，**kwargs 喂参数 + type:ignore
    # 自动注入 initial_cash：strategy `__init__` 接受这个字段（sma_cross /
    # mean_reversion 的 position_pct 路径）就传；不接受的老 strategy 不传。
    strategy_kwargs: dict[str, Any] = dict(params)
    try:
        sig = inspect.signature(strategy_cls.__init__)
        if "initial_cash" in sig.parameters and "initial_cash" not in strategy_kwargs:
            strategy_kwargs["initial_cash"] = initial_cash
    except (TypeError, ValueError):
        pass
    try:
        sig = inspect.signature(strategy_cls.__init__)
        if "position_pct" in sig.parameters and "position_pct" not in strategy_kwargs:
            strategy_kwargs["position_pct"] = 1.0
    except (TypeError, ValueError):
        pass
    strategy = strategy_cls(  # type: ignore[call-arg]
        name=strategy_name,
        clock=engine.clock,
        msgbus=engine.msgbus,
        instrument_id=instrument_id,
        timeframe=timeframe,
        **strategy_kwargs,
    )
    engine.add_strategy(strategy)
    return engine.run(bars)


# datetime → ns 整数转换已上移 kernel.clock.datetime_to_ns(report.py 也要用,
# 留在 runner 会形成 engine → runner 反向依赖)。


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
    ts_ns = datetime_to_ns(dt)

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
