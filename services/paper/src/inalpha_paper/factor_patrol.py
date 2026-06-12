"""因子衰减巡检 —— "衰减值→后期策略方向"闭环的告警端（D-12 · ADR-0047）。

独立后台 task（**不嵌交易 loop**）：周期扫全部 running 的 strategy_run，按
``(venue, symbol, timeframe)`` 分组去重调 factor ``/score``，对比血缘因子的
入场基准 vs 当前衰减态：

- 进入 ``decaying`` → ``run_log`` 一条 warn（code=``factor_decay``，带结构化复盘
  上下文）；同 run×因子只告警一次（状态机存 ``strategy_runs.factor_alerts``）
- 恢复非 decaying → 一条 info（code=``factor_decay_recovered``）+ 状态机重置

边界（ADR-0047 显式不做）：**只告警，不动策略**——不停 runner / 不调仓 / 不剔除
因子；动作决策留给人 / agent 复盘。巡检任何失败只跳过本轮，绝不影响交易循环。

入场基准的两种来源：

- ``lineage``：candidate 落库时声明了 ``factor_snapshot``（author_strategy 的
  factorContext）→ 监控这些因子（走 /score，不受 snapshot 去相关剪枝影响）
- ``environment``：无声明 → 起跑时拍 /snapshot top-N 当"标的因子环境"基准

基准只拍一次（``set_factor_baseline`` 仅 NULL 时写）：它是"入场时"锚点；
起跑时 factor 服务不可用 → 巡检首轮自愈补拍。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import jwt
from inalpha_shared.db import get_conn

from .config import PaperSettings
from .factor_client import FactorClient
from .storage import strategy_candidates as candidates_store
from .storage import strategy_runs as runs_store

_logger = logging.getLogger(__name__)

# 基准/巡检里跳过样本不足的因子——低置信下的 decay_state 是噪声，不该触发告警
_SKIP_LOW_CONFIDENCE = True


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _fmt_ic(v: Any) -> str:
    """rank_ic 类数值格式化，容 None/非数值——原始 JSON dict 字段可能缺失，
    告警文案绝不能因 None:.4f 抛 TypeError 把整条巡检带崩。"""
    try:
        return f"{float(v):.4f}" if v is not None else "n/a"
    except (TypeError, ValueError):
        return "n/a"


def _mint_service_token(settings: PaperSettings, sub: str) -> str:
    """自签短期 service JWT（与 live_runner 同款）；factor 服务只验签不挑身份。"""
    payload = {"sub": sub, "exp": int(time.time()) + settings.live_runner_token_ttl_s}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _eff_to_baseline_row(f: dict[str, Any]) -> dict[str, Any]:
    """FactorEffectiveness dict → 基准行（只留对比需要的字段，控 JSONB 体积）。"""
    return {
        "id": f["factor_id"],
        "rank_ic": f.get("rank_ic"),
        "rank_ic_recent": f.get("rank_ic_recent"),
        "direction": f.get("direction"),
        "decay_state": f.get("decay_state"),
    }


async def capture_factor_baseline(run: dict[str, Any], settings: PaperSettings) -> None:
    """best-effort 拍入场因子基准（起跑时由 live_runner 调；巡检自愈补拍也走这里）。

    已有 baseline 时本函数仍可调（``set_factor_baseline`` 仅 NULL 时写，幂等）。
    任何失败只 log debug——factor 服务可用性绝不影响交易链路。
    """
    run_id: UUID = run["id"]
    try:
        async with get_conn() as conn:
            candidate = await candidates_store.get_candidate(conn, run["candidate_id"])
        lineage = (candidate or {}).get("factor_snapshot") or {}
        lineage_ids = [
            f["id"] for f in lineage.get("factors", []) if isinstance(f, dict) and f.get("id")
        ]

        token = _mint_service_token(settings, str(run["account_id"]))
        async with FactorClient(settings.factor_service_url, token) as fc:
            if lineage_ids:
                resp = await fc.score(
                    venue=run["venue"],
                    symbol=run["symbol"],
                    timeframe=run["timeframe"],
                    factor_ids=lineage_ids,
                )
                factors = resp.get("factors", [])
                source = "lineage"
            else:
                resp = await fc.snapshot(
                    venue=run["venue"], symbol=run["symbol"], timeframe=run["timeframe"]
                )
                factors = resp.get("top_factors", [])
                source = "environment"

        if _SKIP_LOW_CONFIDENCE:
            factors = [f for f in factors if not f.get("low_confidence")]
        if not factors:
            _logger.debug("live run %s: 因子基准为空（样本不足/无可用因子），下轮巡检再试", run_id)
            return

        baseline = {
            "as_of": resp.get("as_of"),
            "venue": run["venue"],
            "symbol": run["symbol"],
            "timeframe": run["timeframe"],
            "source": source,
            "factors": [_eff_to_baseline_row(f) for f in factors],
        }
        async with get_conn() as conn:
            await runs_store.set_factor_baseline(conn, run_id, baseline)
        _logger.info(
            "live run %s: 因子基准已拍（source=%s, %d 个因子）", run_id, source, len(factors)
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _logger.debug("live run %s: 拍因子基准失败（best-effort 跳过）：%s", run_id, e)


class FactorPatrol:
    """进程内单例：因子衰减巡检 task。lifespan 起、shutdown 停。"""

    def __init__(self, *, settings: PaperSettings) -> None:
        self._settings = settings
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._settings.factor_patrol_interval_s <= 0:
            _logger.info("因子衰减巡检已关闭（INALPHA_FACTOR_PATROL_INTERVAL_S=0）")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._patrol_loop(), name="factor-patrol")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None

    async def _patrol_loop(self) -> None:
        """先 sleep 再巡检：起跑时基准刚拍，立即巡检无意义。"""
        interval = self._settings.factor_patrol_interval_s
        while True:
            await asyncio.sleep(interval)
            try:
                await self.patrol_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # 巡检失败只跳过本轮（ADR-0047 D3）：factor/DB 抖动不该杀巡检 task
                _logger.warning("因子衰减巡检本轮失败（已跳过）", exc_info=True)

    async def patrol_once(self) -> None:
        """巡检一轮：补拍缺基准的 run → 分组调 /score → 逐 run 对比告警。"""
        async with get_conn() as conn:
            runs = await runs_store.list_all_running(conn)
        if not runs:
            return

        # 自愈补拍：起跑时 factor 服务不可用的 run 在这里拿到基准（本轮先不对比）
        missing = [r for r in runs if not r.get("factor_baseline")]
        for run in missing:
            await capture_factor_baseline(run, self._settings)
        runs = [r for r in runs if r.get("factor_baseline")]
        if not runs:
            return

        # 按 (venue, symbol, timeframe) 分组去重：同标的多 runner 只打一次 /score
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for run in runs:
            groups.setdefault((run["venue"], run["symbol"], run["timeframe"]), []).append(run)

        for (venue, symbol, timeframe), group_runs in groups.items():
            monitored: set[str] = set()
            for run in group_runs:
                monitored.update(
                    f["id"] for f in run["factor_baseline"].get("factors", []) if f.get("id")
                )
            if not monitored:
                continue
            try:
                token = _mint_service_token(
                    self._settings, str(group_runs[0]["account_id"])
                )
                async with FactorClient(self._settings.factor_service_url, token) as fc:
                    resp = await fc.score(
                        venue=venue,
                        symbol=symbol,
                        timeframe=timeframe,
                        factor_ids=sorted(monitored),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _logger.debug(
                    "因子巡检 %s/%s/%s 调 /score 失败（本轮跳过该组）：%s",
                    venue, symbol, timeframe, e,
                )
                continue
            current_by_id = {f["factor_id"]: f for f in resp.get("factors", [])}
            for run in group_runs:
                try:
                    await self._check_run(run, current_by_id)
                except Exception:
                    _logger.warning(
                        "因子巡检 run %s 对比失败（已跳过）", run["id"], exc_info=True
                    )

    async def _check_run(
        self, run: dict[str, Any], current_by_id: dict[str, dict[str, Any]]
    ) -> None:
        """对比一个 run 的基准 vs 当前，驱动告警状态机（一次告警 / 恢复重置）。"""
        run_id: UUID = run["id"]
        alerts: dict[str, Any] = dict(run.get("factor_alerts") or {})
        changed = False

        for base in run["factor_baseline"].get("factors", []):
            fid = base.get("id")
            cur = current_by_id.get(fid) if fid else None
            if cur is None:
                continue  # 本轮没算出来（数据抖动）→ 保持状态，下轮再看
            if _SKIP_LOW_CONFIDENCE and cur.get("low_confidence"):
                continue  # 样本不足时的 decay_state 是噪声，不驱动状态机
            state = cur.get("decay_state")
            if state is None:
                # factor 服务版本还没 decay_state 字段（滚动升级 paper 先于 factor）：
                # 缺省成 "decaying" 会对每个因子误报一条无依据告警、升级后又收恢复 log，
                # 给复盘添噪。宁可漏告警也不误报——本轮跳过，等服务端补上字段再判。
                continue
            prev = (alerts.get(fid) or {}).get("state")

            if state == "decaying" and prev != "decaying":
                msg = self._alert_msg(run, base, cur)
                async with get_conn() as conn:
                    await runs_store.append_log(
                        conn, run_id, "warn", msg, code="factor_decay"
                    )
                alerts[fid] = {"state": "decaying", "alerted_at": _now_iso()}
                changed = True
            elif state != "decaying" and prev == "decaying":
                async with get_conn() as conn:
                    await runs_store.append_log(
                        conn, run_id, "info",
                        f"因子 {fid} 已从衰减恢复（当前 {state}，"
                        f"rank_ic_recent={_fmt_ic(cur.get('rank_ic_recent'))}）",
                        code="factor_decay_recovered",
                    )
                alerts[fid] = {"state": state, "alerted_at": None}
                changed = True
            elif prev is not None and prev != state:
                alerts[fid] = {**(alerts.get(fid) or {}), "state": state}
                changed = True

        if changed:
            async with get_conn() as conn:
                await runs_store.set_factor_alerts(conn, run_id, alerts)

    @staticmethod
    def _alert_msg(
        run: dict[str, Any], base: dict[str, Any], cur: dict[str, Any]
    ) -> str:
        """告警文案 = 结构化复盘上下文：一条日志看清"依据因子衰减到什么程度 + 现在亏赚"。"""
        pnl = run.get("cumulative_pnl")
        pnl_part = f"，run 累计盈亏 {float(pnl):.2f}" if pnl is not None else ""
        # cur 是 FactorClient.score() 的原始 JSON dict（未做 Pydantic 反序列化校验）：
        # 服务端过渡期格式 / 部分字段缺失时 rank_ic 可能为 None，None:.4f 会抛 TypeError
        # → 一路上溯到 patrol_once 的 except，alerts 永不更新、每轮在同因子上 crash、
        # 告警永远不触发（死循环静默吞）。统一走 _fmt_ic 容 None。
        return (
            f"⚠ 依据因子 {base.get('id')} 进入衰减（decaying）："
            f"入场 rank_ic={_fmt_ic(base.get('rank_ic'))} → "
            f"当前 rank_ic={_fmt_ic(cur.get('rank_ic'))} / "
            f"rank_ic_recent={_fmt_ic(cur.get('rank_ic_recent'))}{pnl_part}。"
            "建议复盘该策略是否仍该跑（系统只告警不动仓，ADR-0047）"
        )
