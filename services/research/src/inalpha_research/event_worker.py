"""Durable RawEvent to EventFact extraction worker."""

from __future__ import annotations

import asyncio

from inalpha_shared import get_logger

from .config import ResearchSettings
from .data_client import DataClient
from .event_extractor import extract_event_fact
from .service_tokens import mint_data_event_token

_logger = get_logger(__name__)


async def run_extraction_once(
    settings: ResearchSettings,
    *,
    worker_id: str,
    limit: int | None = None,
) -> int:
    """Claim and finish one bounded batch, returning the completed job count."""
    token = mint_data_event_token(settings)
    completed = 0
    async with DataClient(settings.data_service_url, token) as data:
        jobs = await data.claim_extraction_jobs(
            worker_id=worker_id,
            limit=limit or settings.event_extraction_batch_size,
            lease_seconds=settings.event_extraction_lease_seconds,
        )
        for job in jobs:
            job_id = str(job.get("job_id") or "")
            lease_token = str(job.get("lease_token") or "")
            raw = job.get("raw_event")
            if not job_id or not lease_token or not isinstance(raw, dict):
                continue
            try:
                fact = extract_event_fact(
                    raw,
                    str(raw.get("policy_version") or "first-seen-only-v1"),
                )
                await data.write_event_fact(fact)
                await data.complete_extraction_job(
                    job_id=job_id,
                    lease_token=lease_token,
                    succeeded=True,
                )
                completed += 1
            except Exception as exc:
                try:
                    await data.complete_extraction_job(
                        job_id=job_id,
                        lease_token=lease_token,
                        succeeded=False,
                        error=f"{type(exc).__name__}: {exc}"[:2_000],
                    )
                except Exception:
                    _logger.exception("failed to release event extraction lease", job_id=job_id)
    return completed


async def run_extraction_worker(
    settings: ResearchSettings,
    stop: asyncio.Event,
    *,
    worker_id: str,
) -> None:
    """Poll the durable outbox until service shutdown."""
    while not stop.is_set():
        try:
            processed = await run_extraction_once(settings, worker_id=worker_id)
        except Exception:
            processed = 0
            _logger.exception("event extraction poll failed")
        if processed:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.event_extraction_poll_seconds)
        except TimeoutError:
            pass


__all__ = ["run_extraction_once", "run_extraction_worker"]
