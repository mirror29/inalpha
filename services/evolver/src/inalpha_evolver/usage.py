"""Evolver-specific attribution layered over the generic request ledger."""

from dataclasses import replace
from typing import Any

from inalpha_shared.db import get_conn
from inalpha_shared.usage import PostgresUsageStore, UsageIdentity


class EvolverUsageStore(PostgresUsageStore):
    """Resolve the persisted approval association; never accept a client-supplied owner."""

    def __init__(self, database_url: str, operation_key: str | None, loop_id: str | None):
        super().__init__(database_url, connection_factory=get_conn)
        self.operation_key = operation_key
        self.loop_id = loop_id

    async def begin(self, identity: UsageIdentity, *args: Any) -> None:
        async with self.connection() as conn:
            cursor = await conn.execute(
                """WITH operations AS (
 SELECT %s::text AS operation_key
 UNION SELECT operation_id FROM evolution_loops
 WHERE loop_id::text=%s AND requested_by_sub=%s
), association AS (
 SELECT invocation_id::text AS invocation_id FROM chat_evolution_operations
 WHERE auth_sub=%s AND operation_id::text IN (SELECT operation_key FROM operations)
 UNION SELECT execution_input->'view'->>'chatInvocationId' FROM evolution_approval_operations
 WHERE auth_sub=%s AND operation_id::text IN (SELECT operation_key FROM operations)
)
SELECT invocation_id FROM association WHERE invocation_id IS NOT NULL
 AND EXISTS(SELECT 1 FROM llm_usage_calls WHERE auth_sub=%s AND operation_id=association.invocation_id)
ORDER BY invocation_id LIMIT 1""",
                (
                    self.operation_key,
                    self.loop_id,
                    identity.auth_sub,
                    identity.auth_sub,
                    identity.auth_sub,
                    identity.auth_sub,
                ),
            )
            row = await cursor.fetchone()
            parent = row["invocation_id"] if row else None
            await self.begin_on_connection(conn, replace(identity, parent_operation_id=parent), *args)
