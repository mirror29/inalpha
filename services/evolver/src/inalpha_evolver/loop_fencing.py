"""Task-local lease scope enforced transactionally at every baseline storage write."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any
from uuid import UUID

from .exceptions import LoopControlError


class BaselineLeaseLost(LoopControlError):
    """A baseline write is no longer owned by this worker."""

    code = "LOOP_BASELINE_LEASE_LOST"


@dataclass(frozen=True, slots=True)
class BaselineLease:
    """Only the durable loop worker installs this scope; it is never request input."""

    loop_id: UUID
    owner_account_id: UUID
    run_id: UUID
    lease_token: UUID


_lease: ContextVar[BaselineLease | None] = ContextVar("evolution_baseline_lease", default=None)


@contextmanager
def baseline_lease(lease: BaselineLease) -> Iterator[None]:
    """Keep ownership local to the worker and its child tasks, not the process."""
    token = _lease.set(lease)
    try:
        yield
    finally:
        _lease.reset(token)


def fenced_baseline_write(function: Callable[..., Any]) -> Callable[..., Any]:
    """Lock the owning loop through the write; standalone E1 keeps its original semantics."""
    @wraps(function)
    async def guarded(conn: Any, run_id: UUID, *args: Any, **kwargs: Any) -> Any:
        async with conn.transaction():
            cursor = await conn.execute(
                """SELECT l.loop_id,l.owner_account_id,l.lease_token,
l.lease_expires_at>=clock_timestamp() AS live,
l.campaign_id IS NULL AND l.status IN ('target_resolved','baseline_ready') AS baseline_active,
a.revoked_at IS NULL AND a.expires_at>clock_timestamp() AS authorized
FROM evolution_loops l JOIN evolution_loop_authorizations a USING(loop_id)
WHERE l.e1_run_id=%s FOR UPDATE OF l,a""",
                (run_id,),
            )
            row = await cursor.fetchone()
            if row is not None:
                cursor = await conn.execute(
                    """SELECT lease_expires_at>=clock_timestamp() AS live FROM evolution_loops
WHERE loop_id=%s""", (row["loop_id"],),
                )
                row["live"] = (await cursor.fetchone())["live"]
            scope = _lease.get()
            if row is not None:
                if (
                    scope is None or scope.run_id != run_id
                    or row["loop_id"] != scope.loop_id
                    or row["owner_account_id"] != scope.owner_account_id
                    or row["lease_token"] != scope.lease_token
                    or not row["live"] or not row["baseline_active"] or not row["authorized"]
                ):
                    raise BaselineLeaseLost("baseline write rejected: loop worker lease is not current")
            elif scope is not None:
                raise BaselineLeaseLost("baseline write rejected: authorized loop is missing")
            return await function(conn, run_id, *args, **kwargs)
    return guarded
