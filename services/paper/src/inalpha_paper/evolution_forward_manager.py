"""Durable Paper-owned observation and replay loop for evolution Forward sandboxes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from inalpha_shared import get_logger
from inalpha_shared.db import get_conn

from .bar_conversion import bar_from_dict
from .config import PaperSettings
from .data_client import DataClient
from .evaluation_executor import KillableEngineRunner
from .event_conversion import market_event_from_fact
from .evolution_execution_policy import frozen_protection_kwargs
from .execution.exchange import EventExecutionPolicy
from .forward_evidence import decide_forward_status, event_reaction_metrics, independent_facts
from .kernel.identifiers import InstrumentId
from .storage import evolution_forward as store
from .strategy_evaluation import evaluate_strategy_source

_logger = get_logger(__name__)


class EvolutionForwardManager:
    """Reconcile isolated Forward rows without using accounts, orders, or Runner state."""

    def __init__(self, settings: PaperSettings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{id(self)}"
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.dispatcher: asyncio.Task[None] | None = None
        self.wake = asyncio.Event()
        self.closing = False

    def start(self) -> None:
        """Start the restart-safe dispatcher."""
        self.dispatcher = asyncio.create_task(self._dispatch(), name="evolution-forward-dispatch")

    async def notify_async(self) -> None:
        self.wake.set()

    async def close(self) -> None:
        self.closing = True
        self.wake.set()
        if self.dispatcher is not None:
            self.dispatcher.cancel()
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(
            *self.tasks.values(),
            *([self.dispatcher] if self.dispatcher else []),
            return_exceptions=True,
        )

    async def _dispatch(self) -> None:
        while not self.closing:
            try:
                if len(self.tasks) >= self.settings.evolution_forward_max_concurrent:
                    await self._wait()
                    continue
                async with get_conn() as conn:
                    sandbox = await store.claim_next_sandbox(
                        conn,
                        worker_id=self.worker_id,
                        ttl_s=max(60, self.settings.job_timeout_s + 60),
                        poll_interval_s=self.settings.evolution_forward_poll_interval_s,
                    )
                if sandbox is None:
                    await self._wait()
                    continue
                task = asyncio.create_task(
                    self._execute(sandbox),
                    name=f"evolution-forward-{sandbox['sandbox_id']}",
                )
                self.tasks[sandbox["sandbox_id"]] = task
                task.add_done_callback(
                    lambda done, sid=sandbox["sandbox_id"]: self._done(sid, done)
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("evolution_forward_dispatch_failed")
                await asyncio.sleep(1)

    async def _execute(self, sandbox: dict[str, Any]) -> None:
        try:
            await self._observe(sandbox)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception(
                "evolution_forward_observation_failed",
                sandbox_id=str(sandbox["sandbox_id"]),
            )
            async with get_conn() as conn:
                await store.release_lease(
                    conn,
                    sandbox["sandbox_id"],
                    sandbox["lease_token"],
                )

    async def _observe(self, sandbox: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        token = self._mint_data_token(sandbox["owner_account_id"])
        async with DataClient(self.settings.data_service_url, token) as client:
            raw_bars, facts = await asyncio.gather(
                client.get_bars(
                    venue=sandbox["venue"],
                    symbol=sandbox["symbol"],
                    timeframe=sandbox["timeframe"],
                    from_ts=sandbox["started_at"],
                    to_ts=now,
                    limit=10_000,
                    fresh=True,
                ),
                client.get_visible_event_facts(
                    asset_id=sandbox["asset_id"],
                    available_after=sandbox["started_at"],
                    cutoff=now,
                ),
            )
        instrument = InstrumentId(symbol=sandbox["symbol"], venue=sandbox["venue"])
        closed_bars = []
        persisted_bars = []
        for payload in raw_bars:
            bar = bar_from_dict(payload, instrument, sandbox["timeframe"])
            if bar.bar_known_at > int(now.timestamp() * 1_000_000_000):
                continue
            closed_bars.append(bar)
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            persisted_bars.append(
                {
                    "bar_open_at": datetime.fromtimestamp(bar.ts_open / 1_000_000_000, tz=UTC),
                    "bar_known_at": datetime.fromtimestamp(
                        bar.bar_known_at / 1_000_000_000, tz=UTC
                    ),
                    "payload": payload,
                    "payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                }
            )
        async with get_conn() as conn:
            written = await store.append_observations(
                conn,
                sandbox_id=sandbox["sandbox_id"],
                lease_token=sandbox["lease_token"],
                bars=persisted_bars,
                facts=facts,
            )
            replay = await store.load_replay_inputs(
                conn,
                sandbox["sandbox_id"],
                sandbox["lease_token"],
            )
        if not written or replay is None:
            return
        result = await self._evaluate(replay, now)
        async with get_conn() as conn:
            updated = await store.persist_replay_result(
                conn,
                sandbox_id=sandbox["sandbox_id"],
                lease_token=sandbox["lease_token"],
                **result,
            )
        if updated is None:
            raise RuntimeError("Forward evidence write lost its fencing token")

    async def _evaluate(self, replay: dict[str, Any], now: datetime) -> dict[str, Any]:
        sandbox = replay["sandbox"]
        instrument = InstrumentId(symbol=sandbox["symbol"], venue=sandbox["venue"])
        bars = [bar_from_dict(item, instrument, sandbox["timeframe"]) for item in replay["bars"]]
        facts = _eligible_facts(replay["facts"], sandbox["hypothesis_spec"])
        independent = independent_facts(facts)
        if len(bars) < 2:
            status = decide_forward_status(
                started_at=sandbox["started_at"],
                deadline_at=sandbox["deadline_at"],
                now=now,
                event_count=len(independent),
                metrics=None,
                risk_alerts=[],
                data_quality_alerts=[],
            )
            return {
                "status": status,
                "event_count": len(independent),
                "metrics": None,
                "risk_alerts": [],
                "data_quality_alerts": [],
                "fills": [],
            }
        versions = sandbox["frozen_versions"]
        events = [market_event_from_fact(item) for item in facts]
        evaluation = await evaluate_strategy_source(
            source_code=sandbox["source_code"],
            bars=bars,
            instrument_id=instrument,
            timeframe=sandbox["timeframe"],
            run_engine=KillableEngineRunner(
                timeout_s=self.settings.job_timeout_s,
                mem_gb=self.settings.job_mem_gb,
            ),
            initial_cash=float(versions.get("initial_cash") or 10_000.0),
            fee_rate=float(versions.get("fee_rate", 0.001)),
            funding_rate=float(versions.get("funding_rate", 0)),
            **frozen_protection_kwargs(versions),
            validation_split=0,
            trading_mode=str(versions.get("trading_mode") or "spot"),
            leverage=int(versions.get("leverage") or 1),
            events=events,
            event_execution_policy=EventExecutionPolicy(),
        )
        report = evaluation.report
        fills = _persisted_fills(report.fills, bars, independent)
        total_slippage = sum(float(item["slippage_cost"]) for item in fills)
        reaction = event_reaction_metrics(
            bars=bars,
            facts=facts,
            direction=str(sandbox["hypothesis_spec"].get("direction") or "long"),
            holding_bars=int(
                (sandbox["hypothesis_spec"].get("invalidation") or {}).get("holding_bars", 4)
            ),
            total_cost_pct=(report.total_fees + total_slippage)
            / max(report.initial_cash, 1.0)
            * 100.0,
        )
        risk_alerts = list(report.health_warnings)
        if report.blew_up:
            risk_alerts.append("account_blew_up")
        data_quality_alerts = _data_quality_alerts(bars)
        metrics = {
            "total_return_pct": report.total_return_pct,
            "sharpe": report.sharpe,
            "max_drawdown_pct": report.max_drawdown_pct,
            "num_trades": report.num_trades,
            "total_fees": report.total_fees,
            "total_slippage_cost": total_slippage,
            **reaction,
        }
        status = decide_forward_status(
            started_at=sandbox["started_at"],
            deadline_at=sandbox["deadline_at"],
            now=now,
            event_count=len(independent),
            metrics=metrics,
            risk_alerts=risk_alerts,
            data_quality_alerts=data_quality_alerts,
        )
        return {
            "status": status,
            "event_count": len(independent),
            "metrics": metrics,
            "risk_alerts": risk_alerts,
            "data_quality_alerts": data_quality_alerts,
            "fills": fills,
        }

    def _mint_data_token(self, owner_account_id: UUID) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": str(owner_account_id),
                "token_use": "service",
                "service_audience": "data",
                "token_purpose": "event_snapshot_read",
                "owner_account_id": str(owner_account_id),
                "iat": now,
                "exp": now + min(self.settings.live_runner_token_ttl_s, 300),
            },
            self.settings.jwt_secret,
            algorithm=self.settings.jwt_algorithm,
        )

    def _done(self, sandbox_id: UUID, task: asyncio.Task[None]) -> None:
        self.tasks.pop(sandbox_id, None)
        self.wake.set()
        if not task.cancelled():
            task.exception()

    async def _wait(self) -> None:
        self.wake.clear()
        try:
            await asyncio.wait_for(
                self.wake.wait(),
                timeout=self.settings.evolution_forward_poll_interval_s,
            )
        except TimeoutError:
            pass


def _eligible_facts(
    facts: list[dict[str, Any]],
    hypothesis: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply the locked hypothesis scope before counting or delivering events."""
    types = set(hypothesis.get("event_types") or ())
    risk = hypothesis.get("risk") or {}
    return [
        fact
        for fact in facts
        if (not types or fact["event_type"] in types)
        and float(fact.get("severity") or 0) >= float(risk.get("min_severity") or 0)
        and float(fact.get("confidence") or 0) >= float(risk.get("min_confidence") or 0)
    ]


def _persisted_fills(
    fills: list[Any], bars: list[Any], facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Derive stable fill keys, conservative slippage, and nearest causal fact references."""
    result: list[dict[str, Any]] = []
    for index, fill in enumerate(fills):
        filled_at = datetime.fromtimestamp(fill.ts_ns / 1_000_000_000, tz=UTC)
        nearest_bar = min(bars, key=lambda bar: abs(bar.bar_known_at - fill.ts_ns))
        slippage = abs(float(fill.fill_price) - float(nearest_bar.open)) * float(fill.quantity)
        causal_cutoff = filled_at - timedelta(hours=24)
        eligible = [
            fact
            for fact in facts
            if causal_cutoff <= fact["available_at"] <= filled_at
        ]
        nearest_fact = max(eligible, key=lambda fact: fact["available_at"], default=None)
        fact_id = str(nearest_fact["fact_id"]) if nearest_fact else None
        material = [
            fill.ts_ns,
            index,
            fill.side,
            fill.quantity,
            fill.fill_price,
            fill.fee,
        ]
        result.append(
            {
                "fill_key": hashlib.sha256(
                    json.dumps(material, separators=(",", ":")).encode()
                ).hexdigest(),
                "fact_id": fact_id,
                "filled_at": filled_at,
                "side": fill.side,
                "quantity": fill.quantity,
                "price": fill.fill_price,
                "fee": fill.fee,
                "slippage_cost": slippage,
            }
        )
    return result


def _data_quality_alerts(bars: list[Any]) -> list[str]:
    """Reject physically invalid OHLCV while leaving market-session gaps to Data calendars."""
    return [
        "invalid_ohlcv"
        for bar in bars
        if bar.low > min(bar.open, bar.close)
        or bar.high < max(bar.open, bar.close)
        or bar.low <= 0
        or bar.volume < 0
    ][:1]


__all__ = ["EvolutionForwardManager"]
