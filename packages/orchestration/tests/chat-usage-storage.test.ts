import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { afterAll, describe, expect, it } from "vitest";
import { createChatUsageStore, type ChatCallIdentity } from "../src/mastra/llm/chat-usage-store.js";
import { normalizeChatUsage } from "../src/mastra/llm/chat-usage.js";
import { buildEvolutionLLMSnapshot } from "../src/mastra/llm/evolution-snapshot.js";

const url = process.env.INALPHA_MIGRATION_TEST_DATABASE_URL;
let pool: Pool | undefined;
/** Connects only to this task's fresh local migration database. */
function database(): Pool {
  if (!pool) {
    const address = new URL(url!.replace("postgresql+psycopg://", "postgresql://"));
    if (!["localhost", "127.0.0.1", "[::1]"].includes(address.hostname)
      || !/^\/inalpha_migration_.*_test$/.test(address.pathname)) throw new Error("owned local test database required");
    pool = new Pool({ connectionString: address.toString(), max: 2 });
  }
  return pool;
}
function call(): ChatCallIdentity {
  return { callId: randomUUID(), invocationId: randomUUID(), authSub: "alice", stepNumber: 0, source: "chat",
    provider: "deepseek", model: "deepseek-flash", configId: "owned-config",
    pricing: buildEvolutionLLMSnapshot({ id: "owned-config", provider: "deepseek", model: "deepseek-flash", api_key: "SECRET" }).pricing };
}
afterAll(async () => { await pool?.end(); });

describe.skipIf(!url)("owner-scoped chat call storage", () => {
  it("retains unknown calls across store recreation and exact small estimates after settlement", async () => {
    const db = database(); const identity = call();
    await createChatUsageStore(db).begin(identity);
    let row = (await db.query("SELECT * FROM chat_usage_receipts WHERE call_id=$1", [identity.callId])).rows[0];
    expect(row.usage_status).toBe("unknown");
    expect(row.estimated_cost_usd).toBeNull();
    expect(JSON.stringify(row)).not.toContain("SECRET");
    await createChatUsageStore(db).settle(identity, normalizeChatUsage({ inputTokens: 1, outputTokens: 1 }, identity.pricing));
    row = (await db.query("SELECT * FROM chat_usage_receipts WHERE call_id=$1", [identity.callId])).rows[0];
    expect(row.estimated_cost_usd).toBe("0.000001500000");
    expect(row.usage_status).toBe("known");
  });

  it("rejects cross-owner or cross-invocation settlement and never overwrites the first receipt", async () => {
    const db = database(); const identity = call(); const store = createChatUsageStore(db);
    const receipt = normalizeChatUsage({ inputTokens: 1, outputTokens: 1 }, identity.pricing);
    await store.begin(identity);
    await store.settle({ ...identity, authSub: "bob" }, receipt);
    await store.settle({ ...identity, invocationId: randomUUID() }, receipt);
    expect((await db.query("SELECT settled_at FROM chat_usage_receipts WHERE call_id=$1", [identity.callId])).rows[0].settled_at).toBeNull();
    await store.settle(identity, receipt);
    await store.settle(identity, normalizeChatUsage({ inputTokens: 999, outputTokens: 999 }, identity.pricing));
    expect((await db.query("SELECT input_tokens FROM chat_usage_receipts WHERE call_id=$1", [identity.callId])).rows[0].input_tokens).toBe("1");
    await expect(store.begin(identity)).rejects.toThrow();
  });
});
