import type { Pool } from "pg";
import { getChatUsagePool } from "./chat-usage-store.js";

export type ChatOperationLink = {
  authSub: string;
  operationId: string;
  invocationId: string;
  toolName: "evolver.start_evolution_loop" | "evolver.run_event_campaign";
};

/** Records first preparation provenance; a reused operation never changes its origin. */
export async function linkChatOperation(
  link: ChatOperationLink,
  pool: Pick<Pool, "query"> = getChatUsagePool(),
): Promise<void> {
  const result = await pool.query(
    `INSERT INTO chat_evolution_operations(auth_sub,operation_id,invocation_id,tool_name)
     SELECT $1,$2,$3,$4 WHERE EXISTS (
       SELECT 1 FROM chat_usage_receipts WHERE auth_sub=$1 AND invocation_id=$3 AND call_source='chat'
     ) ON CONFLICT(auth_sub,operation_id) DO UPDATE SET operation_id=EXCLUDED.operation_id
     RETURNING operation_id`,
    [link.authSub, link.operationId, link.invocationId, link.toolName],
  );
  if (result.rowCount !== 1) throw new Error("trusted chat call receipt unavailable");
}
