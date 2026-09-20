"""Persistent automatic baseline and campaign handoff independent of chat lifetime."""

import asyncio
import socket
from typing import Any
from uuid import UUID

from inalpha_shared import get_logger
from inalpha_shared.db import get_conn

from ..config import EvolverSettings
from ..loop_fencing import BaselineLeaseLost
from ..storage import loop_dispatch
from .loop_baseline import execute_loop_baseline
from .loop_campaign import handoff_campaign
from .retry_policy import primary_exception, retryable_failure

_logger = get_logger(__name__)


class LoopManager:
    """Own only explicitly authorized loops; legacy E1 and manual campaigns remain separate."""

    def __init__(self, settings: EvolverSettings, campaign_manager: Any | None = None) -> None:
        self.settings = settings
        self.campaign_manager = campaign_manager
        self.worker_id = f"loop:{socket.gethostname()}:{id(self)}"
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.dispatcher: asyncio.Task[None] | None = None
        self.wake = asyncio.Event()
        self.closing = False
        self.unhealthy_reason: str | None = None

    @property
    def healthy(self) -> bool:
        return bool(self.dispatcher and not self.dispatcher.done() and not self.unhealthy_reason)

    async def start(self) -> None:
        """Polling, not an HTTP background callback, owns workflow progress."""
        if not self.settings.event_evolution_enabled:
            raise RuntimeError("automatic loops require event evolution capability")
        self.dispatcher = asyncio.create_task(self._dispatch(), name="evolution-loop-dispatch")

    async def notify_async(self) -> None:
        self.wake.set()

    async def close(self) -> None:
        """Cancel local work and release its lease without aborting durable research."""
        self.closing = True
        self.wake.set()
        tasks = [*self.tasks.values(), *([self.dispatcher] if self.dispatcher else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispatch(self) -> None:
        while not self.closing:
            try:
                if len(self.tasks) >= self.settings.evolver_max_running_runs:
                    await self._wait()
                    continue
                async with get_conn() as conn:
                    loop = await loop_dispatch.claim_next(
                        conn, worker_id=self.worker_id, authorized_only=True,
                        ttl_s=self.settings.campaign_lease_ttl_s,
                    )
                self.unhealthy_reason = None
                if loop is None:
                    await self._wait()
                    continue
                task = asyncio.create_task(self._execute(loop), name=f"loop-{loop['loop_id']}")
                self.tasks[loop["loop_id"]] = task
                task.add_done_callback(lambda done, lid=loop["loop_id"]: self._done(lid, done))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.unhealthy_reason = f"loop dispatcher failed: {type(exc).__name__}"
                await self._wait()

    async def _work(self, loop: dict[str, Any]) -> None:
        async with asyncio.timeout(self.settings.evolver_run_timeout_s):
            await execute_loop_baseline(loop, self.settings)
            await handoff_campaign(loop, self.settings)
        if self.campaign_manager is not None:
            await self.campaign_manager.notify_async()

    async def _execute(self, loop: dict[str, Any]) -> None:
        scope = {key: loop[key] for key in ("loop_id", "owner_account_id", "lease_token")}
        try:
            async with asyncio.TaskGroup() as group:
                work = group.create_task(self._work(loop))
                group.create_task(self._heartbeat(scope, work))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = primary_exception(exc)
            if not isinstance(failure, BaselineLeaseLost):
                code = str(getattr(failure, "code", "EVOLUTION_LOOP_FAILED"))
                async with get_conn() as conn:
                    await loop_dispatch.record_failure(
                        conn, **scope, code=code, message=str(failure),
                        retryable=retryable_failure(failure),
                    )
                _logger.warning("evolution_loop_step_failed", loop_id=str(loop["loop_id"]), code=code)
        finally:
            async with get_conn() as conn:
                await loop_dispatch.defer(conn, **scope, delay_s=0)

    async def _heartbeat(self, scope: dict[str, Any], work: asyncio.Task[None]) -> None:
        while not work.done():
            await asyncio.wait({work}, timeout=self.settings.campaign_lease_ttl_s / 3)
            if work.done():
                return
            async with get_conn() as conn:
                if not await loop_dispatch.renew(
                    conn, **scope, ttl_s=self.settings.campaign_lease_ttl_s,
                ):
                    raise BaselineLeaseLost("loop lease expired or was replaced")

    def _done(self, loop_id: UUID, task: asyncio.Task[None]) -> None:
        self.tasks.pop(loop_id, None)
        self.wake.set()
        if not task.cancelled() and task.exception() is not None:
            _logger.error("evolution_loop_dispatch_task_failed", loop_id=str(loop_id))

    async def _wait(self) -> None:
        self.wake.clear()
        try:
            await asyncio.wait_for(self.wake.wait(), timeout=1)
        except TimeoutError:
            pass
