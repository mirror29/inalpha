"""Lease-backed campaign dispatcher that survives process restarts."""

from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from inalpha_shared import get_logger
from inalpha_shared.db import get_conn

from ..config import EvolverSettings
from ..storage import campaigns as store
from ..storage import loops
from .campaign import execute_campaign
from .retry_policy import primary_exception, retryable_failure

_logger = get_logger(__name__)


class CampaignManager:
    """Dispatch E2 campaigns separately from the backward-compatible E1 run queue."""

    def __init__(self, settings: EvolverSettings) -> None:
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{id(self)}"
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.slots = asyncio.Semaphore(settings.campaign_max_concurrent)
        self.dispatcher: asyncio.Task[None] | None = None
        self.wake = asyncio.Event()
        self.closing = False
        self.unhealthy_reason: str | None = None

    @property
    def healthy(self) -> bool:
        return bool(self.dispatcher and not self.dispatcher.done() and not self.unhealthy_reason)

    async def start(self) -> None:
        """Start polling; interrupted campaigns remain replaying until their lease expires."""
        self.dispatcher = asyncio.create_task(self._dispatch(), name="campaign-dispatch")

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
                if len(self.tasks) >= self.settings.campaign_max_concurrent:
                    await self._wait()
                    continue
                async with get_conn() as conn:
                    campaign = await store.claim_next_campaign(
                        conn,
                        worker_id=self.worker_id,
                        ttl_s=self.settings.campaign_lease_ttl_s,
                    )
                self.unhealthy_reason = None
                if campaign is None:
                    await self._wait()
                    continue
                task = asyncio.create_task(
                    self._execute(campaign),
                    name=f"campaign-{campaign['campaign_id']}",
                )
                self.tasks[campaign["campaign_id"]] = task
                task.add_done_callback(
                    lambda done, cid=campaign["campaign_id"]: self._done(cid, done)
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.unhealthy_reason = f"campaign dispatcher failed: {type(exc).__name__}: {exc}"
                await asyncio.sleep(1)

    async def _execute(self, campaign: dict[str, Any]) -> None:
        async with self.slots:
            try:
                async with asyncio.TaskGroup() as group:
                    work = group.create_task(execute_campaign(campaign, self.settings))
                    group.create_task(self._heartbeat(campaign, work))
                self.unhealthy_reason = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = _primary_exception(exc)
                failure_message = str(getattr(failure, "message", failure))
                failure_code = str(getattr(failure, "code", "CAMPAIGN_FAILED"))
                retryable = retryable_failure(failure)
                retry_status = (
                    str(campaign["status"])
                    if campaign.get("status")
                    in {"candidate_locked", "waiting_forward", "holdout_ready"}
                    else "replaying"
                )
                _logger.exception(
                    "campaign_execution_failed",
                    campaign_id=str(campaign["campaign_id"]),
                    failure_type=type(failure).__name__,
                    failure_code=failure_code,
                    failure_message=failure_message,
                    retryable=retryable,
                )
                async with get_conn() as conn:
                    await store.transition(
                        conn,
                        campaign["campaign_id"],
                        campaign["owner_account_id"],
                        from_statuses=(retry_status,),
                        to_status=retry_status if retryable else "failed",
                        values={
                            "failure_code": failure_code,
                            "failure_message": failure_message[:1000],
                            "finished_at": None if retryable else datetime.now(UTC),
                            "lease_owner": None,
                            "lease_token": None,
                            "lease_expires_at": (
                                datetime.now(UTC) + timedelta(seconds=30) if retryable else None
                            ),
                        },
                        lease_token=campaign["lease_token"],
                    )
            finally:
                try:
                    async with get_conn() as conn:
                        await loops.get_loop_by_campaign(
                            conn, campaign["campaign_id"], campaign["owner_account_id"],
                        )
                except Exception:
                    _logger.warning("campaign_loop_projection_delayed", campaign_id=str(campaign["campaign_id"]))

    async def _heartbeat(
        self,
        campaign: dict[str, Any],
        work: asyncio.Task[None],
    ) -> None:
        """Renew the fencing token until work completes; lease loss cancels the worker."""
        interval = max(1.0, self.settings.campaign_lease_ttl_s / 3)
        while not work.done():
            await asyncio.wait({work}, timeout=interval)
            if work.done():
                return
            async with get_conn() as conn:
                renewed = await store.renew_lease(
                    conn,
                    campaign["campaign_id"],
                    worker_id=self.worker_id,
                    lease_token=campaign["lease_token"],
                    ttl_s=self.settings.campaign_lease_ttl_s,
                )
            if not renewed:
                raise RuntimeError("campaign lease fencing token was lost")

    def _done(self, campaign_id: UUID, task: asyncio.Task[None]) -> None:
        self.tasks.pop(campaign_id, None)
        self.wake.set()
        if not task.cancelled():
            task.exception()

    async def _wait(self) -> None:
        self.wake.clear()
        try:
            await asyncio.wait_for(self.wake.wait(), timeout=1.0)
        except TimeoutError:
            pass


def _primary_exception(exc: Exception) -> Exception:
    """Unwrap TaskGroup failures so persisted campaign errors remain actionable."""
    return primary_exception(exc)


__all__ = ["CampaignManager"]
