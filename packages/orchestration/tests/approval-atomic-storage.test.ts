import { randomUUID } from "node:crypto";
import { Pool } from "pg";
import { afterAll, describe, expect, it } from "vitest";
import { PendingApprovalsStore, approvalInputDigest, type ApprovedExecutionCommand } from "../src/permissions/pending.js";
import { approveEvolutionExecution, findApprovedExecution, findEvolutionOperation, insertPending, markResolved, rememberEvolutionOperation, setPool } from "../src/permissions/repo.js";

const url = process.env.INALPHA_MIGRATION_TEST_DATABASE_URL;
const enabled = Boolean(url);
let pool: Pool | undefined;

/** Real storage checks use only the explicitly owned local migration test database. */
function database(): Pool {
  if (!pool) {
    const address = new URL(url!.replace("postgresql+psycopg://", "postgresql://"));
    if (!["localhost", "127.0.0.1", "[::1]"].includes(address.hostname) || !/^\/inalpha_migration_.*_test$/.test(address.pathname)) {
      throw new Error("atomic approval tests require a dedicated local migration test database");
    }
    pool = new Pool({ connectionString: address.toString(), max: 2 });
    setPool(pool);
  }
  return pool;
}

function command(sessionId = randomUUID()): ApprovedExecutionCommand {
  const approvalInput = { request: { seedStrategyId: "seed", budget: 4 } };
  return { approvalInput, view: { requestId: randomUUID(), toolName: "evolver.run_evolution",
    toolInput: { request: { seedStrategyId: "seed", budget: 4 }, llm_snapshot: { config_id: "owned-model" } },
    inputDigest: approvalInputDigest(approvalInput), sessionId,
    chatInvocationId: randomUUID(), createdAt: new Date().toISOString(), deadline: new Date(Date.now() + 30_000).toISOString() } };
}

/** Seeds a verified local chat call before its approval transaction. */
async function chatReceipt(saved: ApprovedExecutionCommand): Promise<void> {
  await database().query(`INSERT INTO chat_usage_receipts(call_id,invocation_id,auth_sub,step_number,call_source)
    VALUES($1,$2,'alice',0,'chat')`, [randomUUID(), saved.view.chatInvocationId]);
}

afterAll(async () => { setPool(undefined); await pool?.end(); });

describe.skipIf(!enabled)("atomic owner approval storage", () => {
  it("retains original preparation audit when an expired identical approval is replaced", async () => {
    const db = database();
    const first = command();
    await chatReceipt(first);
    await approveEvolutionExecution({ authSub: "alice", command: first, retentionMs: 60_000 });
    await db.query("UPDATE evolution_approval_operations SET approved_at=NOW()-INTERVAL '2 hours',expires_at=NOW()-INTERVAL '1 hour' WHERE operation_id=$1", [first.view.requestId]);
    const second = command(first.view.sessionId);
    await chatReceipt(second);
    await approveEvolutionExecution({ authSub: "alice", command: second, retentionMs: 60_000 });
    expect(await findApprovedExecution(first.view.requestId, "alice")).toBeUndefined();
    expect(await findApprovedExecution(second.view.requestId, "alice")).toEqual(second);
    for (const saved of [first, second]) {
      const row = await db.query("SELECT invocation_id FROM chat_evolution_operations WHERE auth_sub='alice' AND operation_id=$1", [saved.view.requestId]);
      expect(row.rows[0].invocation_id).toBe(saved.view.chatInvocationId);
    }
    const missing = command();
    await expect(approveEvolutionExecution({ authSub: "alice", command: missing, retentionMs: 60_000 })).rejects.toThrow("trusted chat provenance unavailable");
    expect((await db.query("SELECT status FROM pending_approvals WHERE request_id=$1", [missing.view.requestId])).rows).toEqual([]);
  });

  it("persists consent and frozen input together and recovers before any execution", async () => {
    const db = database();
    const store = new PendingApprovalsStore(() => {}, { approveEvolutionExecution, findApprovedExecution,
      findEvolutionOperation, insertPending, markResolved, rememberEvolutionOperation });
    const original = command();
    await chatReceipt(original);
    const view = store.request({ ...original.view, authSub: "alice", approvalInput: original.approvalInput });
    expect(await store.respondTrusted(view.requestId, "allow", "alice")).toBe(true);
    const row = await db.query("SELECT status FROM pending_approvals WHERE request_id=$1", [view.requestId]);
    expect(row.rows[0].status).toBe("allowed");
    store.clearAll();
    const restarted = new PendingApprovalsStore(() => {}, { approveEvolutionExecution, findApprovedExecution,
      findEvolutionOperation, insertPending, markResolved, rememberEvolutionOperation });
    const calls: string[] = [];
    restarted.registerApprovedExecution("evolver.run_evolution", async (saved) => { calls.push(saved.view.requestId); return { run_id: "same-run" }; });
    expect(await restarted.status(view.requestId, "bob")).toEqual({ status: "unavailable" });
    expect(await restarted.status(view.requestId, "alice")).toEqual({ status: "approved" });
    expect(calls).toEqual([]);
    expect(await restarted.dispatchApproved(view.requestId, "alice", {})).toEqual({ run_id: "same-run" });
    expect(calls).toEqual([view.requestId]);
    restarted.clearAll();
  });

  it("rolls back the decision when the operation identity conflicts", async () => {
    const db = database();
    const first = command();
    await chatReceipt(first);
    await approveEvolutionExecution({ authSub: "alice", command: first, retentionMs: 60_000 });
    const second = command(first.view.sessionId);
    await expect(approveEvolutionExecution({ authSub: "alice", command: second, retentionMs: 60_000 })).rejects.toThrow("approved operation already exists");
    expect((await db.query("SELECT status FROM pending_approvals WHERE request_id=$1", [second.view.requestId])).rows).toEqual([]);
    expect(await findApprovedExecution(first.view.requestId, "alice")).toEqual(first);
    expect(await findApprovedExecution(first.view.requestId, "bob")).toBeUndefined();
  });

  it("never approves expired input or changes another owner's existing decision", async () => {
    const db = database();
    const expired = command();
    expired.view.deadline = new Date(Date.now() - 1_000).toISOString();
    await expect(approveEvolutionExecution({ authSub: "alice", command: expired, retentionMs: 60_000 })).rejects.toThrow("approval no longer pending");
    expect(await findApprovedExecution(expired.view.requestId, "alice")).toBeUndefined();
    const owned = command();
    await insertPending(owned.view, "alice");
    await expect(approveEvolutionExecution({ authSub: "bob", command: owned, retentionMs: 60_000 })).rejects.toThrow("approval no longer pending");
    expect((await db.query("SELECT status FROM pending_approvals WHERE request_id=$1", [owned.view.requestId])).rows[0].status).toBe("pending");
  });
});
