import { Pool } from "pg";
import { getSettings } from "../../config.js";
import type { ChatUsageReceipt } from "./chat-usage.js";
import type { EvolutionPricingSnapshot } from "./evolution-snapshot.js";

export type ChatCallIdentity = {
  callId: string;
  invocationId: string;
  authSub: string;
  source: "chat" | "service";
  stepNumber: number;
  provider: string | null;
  model: string | null;
  configId: string | null;
  pricing?: EvolutionPricingSnapshot;
};

export interface ChatUsageStore {
  begin(call: ChatCallIdentity): Promise<void>;
  settle(call: ChatCallIdentity, receipt: ChatUsageReceipt): Promise<void>;
}

/** Persists an unknown call before fees; settlement is owner-bound and once only. */
export function createChatUsageStore(pool: Pick<Pool, "query">): ChatUsageStore {
  return {
    async begin(call) {
      await pool.query(
        `INSERT INTO chat_usage_receipts
         (call_id,invocation_id,auth_sub,step_number,provider,model,config_id,pricing,call_source)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)`,
        [call.callId, call.invocationId, call.authSub, call.stepNumber,
          call.provider, call.model, call.configId, call.pricing ? JSON.stringify(call.pricing) : null, call.source],
      );
    },
    async settle(call, receipt) {
      await pool.query(
        `UPDATE chat_usage_receipts SET usage_status=$4,input_tokens=$5,output_tokens=$6,
         cached_input_tokens=$7,estimated_cost_usd=$8,settled_at=NOW()
         WHERE call_id=$1 AND invocation_id=$2 AND auth_sub=$3 AND settled_at IS NULL`,
        [call.callId, call.invocationId, call.authSub, receipt.usageStatus,
          receipt.inputTokens, receipt.outputTokens, receipt.cachedInputTokens, receipt.estimatedCostUsd],
      );
    },
  };
}

let productionPool: Pool | undefined;

/** Shares a lazy production ledger connection pool without exposing credentials. */
export function getChatUsagePool(): Pool {
  if (!productionPool) {
    const databaseUrl = getSettings().databaseUrl;
    if (!databaseUrl) throw new Error("chat usage database unavailable");
    productionPool = new Pool({ connectionString: databaseUrl, max: 3,
      connectionTimeoutMillis: 5000, statement_timeout: 5000 });
  }
  return productionPool;
}

/** Fails before model payment if the durable receipt store is unavailable. */
export function createProductionChatUsageStore(): ChatUsageStore {
  return {
    begin: (call) => createChatUsageStore(getChatUsagePool()).begin(call),
    settle: (call, receipt) => createChatUsageStore(getChatUsagePool()).settle(call, receipt),
  };
}
