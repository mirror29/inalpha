import { afterEach, describe, expect, it, vi } from "vitest";
import { HookRunner } from "../src/hooks/runner.js";
import { AUTH_SUB_KEY, withHooks } from "../src/hooks/with-hooks.js";
import { USER_LLM_SNAPSHOT_KEY, APPROVAL_OPERATION_ID_KEY } from "../src/mastra/llm/evolution-snapshot.js";
import { PendingApprovalsStore } from "../src/permissions/pending.js";

const stores: PendingApprovalsStore[] = [];
afterEach(() => stores.splice(0).forEach((store) => store.clearAll()));

function setup() {
  const store = new PendingApprovalsStore(() => {});
  stores.push(store);
  const view = store.request({
    toolName: "evolver.run_evolution", authSub: "alice", sessionId: "thread",
    toolInput: { request: { seedStrategyId: "candidate:seed", budget: 4 }, llm_snapshot: { config_id: "owned-model" } },
    approvalInput: { frozen: "digest" }, timeoutMs: 30_000,
  });
  return { store, view };
}

describe("approved frozen dispatch", () => {
  it("submits the frozen request without another model turn and shares concurrent retries", async () => {
    const { store, view } = setup();
    const execute = vi.fn(async (command) => ({ run_id: "run-1", seed: command.view.toolInput.request.seedStrategyId }));
    store.registerApprovedExecution("evolver.run_evolution", execute);
    expect(await store.dispatchApproved(view.requestId, "alice", {})).toBeUndefined();
    expect(store.respond(view.requestId, "allow", "alice")).toBe(true);
    const [first, second] = await Promise.all([
      store.dispatchApproved(view.requestId, "alice", {}),
      store.dispatchApproved(view.requestId, "alice", {}),
    ]);
    expect(first).toEqual({ run_id: "run-1", seed: "candidate:seed" });
    expect(second).toEqual(first);
    expect(execute).toHaveBeenCalledTimes(1);
    expect(await store.dispatchApproved(view.requestId, "bob", {})).toBeUndefined();
  });
});


it("rechecks frozen input through hooks and refuses changed preparation without another ask", async () => {
  const store = new PendingApprovalsStore(() => {});
  stores.push(store);
  let drift = false;
  const execute = vi.fn(async () => ({ run_id: "run" }));
  const tool = withHooks({ id: "evolver.run_evolution", execute }, {
    runner: new HookRunner(), pendingApprovals: store, permissionResolver: () => "ask",
    approvalPreflight: async (_tool, input) => ({ input: { ...(input as object), preparation: drift ? "changed" : "frozen" } }),
  });
  const requestContext = new Map<string, unknown>([
    [AUTH_SUB_KEY, "alice"], ["sessionId", "thread"], [USER_LLM_SNAPSHOT_KEY, { config_id: "owned-model" }],
  ]);
  const pending = await tool.execute!({ seedStrategyId: "seed", budget: 4 }, { requestContext }) as { requestId: string };
  store.respond(pending.requestId, "allow", "alice");
  drift = true;
  expect(await store.dispatchApproved(pending.requestId, "alice", { requestContext })).toMatchObject({ deniedBy: "approval-input-drift" });
  expect(execute).not.toHaveBeenCalled();
  expect(store.list("alice")).toEqual([]);
});

it("uses the frozen snapshot and operation under the fresh authenticated owner", async () => {
  const store = new PendingApprovalsStore(() => {});
  stores.push(store);
  const execute = vi.fn(async (_input, ctx) => ({ run_id: "run", operation: ctx.requestContext.get(APPROVAL_OPERATION_ID_KEY),
    owner: ctx.requestContext.get(AUTH_SUB_KEY), model: ctx.requestContext.get(USER_LLM_SNAPSHOT_KEY) }));
  const tool = withHooks({ id: "evolver.run_evolution", execute }, {
    runner: new HookRunner(), pendingApprovals: store, permissionResolver: () => "ask",
  });
  const snapshot = { config_id: "frozen-model" };
  const requestContext = new Map<string, unknown>([[AUTH_SUB_KEY, "alice"], ["sessionId", "thread"], [USER_LLM_SNAPSHOT_KEY, snapshot]]);
  const pending = await tool.execute!({ seedStrategyId: "seed", budget: 4 }, { requestContext }) as { requestId: string };
  snapshot.config_id = "mutated-after-request";
  store.respond(pending.requestId, "allow", "alice");
  expect(await store.dispatchApproved(pending.requestId, "alice", { requestContext })).toEqual({ run_id: "run", operation: pending.requestId,
    owner: "alice", model: { config_id: "frozen-model" } });
});

it("fails closed when the approval operation cannot be durably recorded", async () => {
  const persistence = { insertPending: vi.fn(async () => {}), markResolved: vi.fn(async () => {}),
    findEvolutionOperation: vi.fn(async () => undefined), rememberEvolutionOperation: vi.fn(async () => { throw new Error("database unavailable"); }) };
  const store = new PendingApprovalsStore(() => {}, persistence);
  stores.push(store);
  const execute = vi.fn();
  store.registerApprovedExecution("evolver.run_evolution", execute);
  const view = store.request({ authSub: "alice", sessionId: "thread", toolName: "evolver.run_evolution", toolInput: {}, approvalInput: {} });
  store.respond(view.requestId, "allow", "alice");
  await expect(store.dispatchApproved(view.requestId, "alice", {})).rejects.toThrow("approved execution unavailable");
  expect(execute).not.toHaveBeenCalled();
});

it("recovers the same non-secret command and operation after a store restart", async () => {
  let saved: any;
  const persistence = { insertPending: vi.fn(async () => {}), markResolved: vi.fn(async () => {}),
    rememberEvolutionOperation: vi.fn(async (args) => { saved = structuredClone(args); return { expiresAt: new Date(Date.now() + 60_000).toISOString() }; }),
    findEvolutionOperation: vi.fn(async (args) => saved && args.authSub === saved.authSub && args.inputDigest === saved.inputDigest
      ? { operationId: saved.operationId, expiresAt: new Date(Date.now() + 60_000).toISOString() } : undefined),
    findApprovedExecution: vi.fn(async (id, owner) => saved?.operationId === id && saved.authSub === owner ? structuredClone(saved.execution) : undefined),
  };
  const first = new PendingApprovalsStore(() => {}, persistence);
  stores.push(first);
  first.registerApprovedExecution("evolver.run_evolution", async () => ({ run_id: "run-1" }));
  const view = first.request({ authSub: "alice", sessionId: "thread", toolName: "evolver.run_evolution", toolInput: { request: "frozen" }, approvalInput: { frozen: true } });
  first.respond(view.requestId, "allow", "alice");
  await first.dispatchApproved(view.requestId, "alice", {});
  first.clearAll();
  const restarted = new PendingApprovalsStore(() => {}, persistence);
  stores.push(restarted);
  const execute = vi.fn(async (command) => ({ run_id: "run-1", input: command.view.toolInput }));
  restarted.registerApprovedExecution("evolver.run_evolution", execute);
  expect(await restarted.dispatchApproved(view.requestId, "bob", {})).toBeUndefined();
  expect(await restarted.dispatchApproved(view.requestId, "alice", {})).toEqual({ run_id: "run-1", input: { request: "frozen" } });
  expect(persistence.rememberEvolutionOperation).toHaveBeenCalledTimes(1);
  expect(execute).toHaveBeenCalledTimes(1);
});


it("does not report consent as accepted when the atomic commit fails", async () => {
  const persistence = { insertPending: vi.fn(async () => {}), markResolved: vi.fn(async () => {}),
    findEvolutionOperation: vi.fn(async () => undefined), rememberEvolutionOperation: vi.fn(async () => undefined),
    approveEvolutionExecution: vi.fn(async () => { throw new Error("commit failed"); }) };
  const store = new PendingApprovalsStore(() => {}, persistence);
  stores.push(store);
  const execute = vi.fn();
  store.registerApprovedExecution("evolver.run_evolution", execute);
  const view = store.request({ authSub: "alice", sessionId: "thread", toolName: "evolver.run_evolution", toolInput: {}, approvalInput: {} });
  await expect(store.respondTrusted(view.requestId, "allow", "alice")).rejects.toThrow("commit failed");
  expect(await store.status(view.requestId, "alice")).toMatchObject({ status: "pending" });
  expect(await store.dispatchApproved(view.requestId, "alice", {})).toBeUndefined();
  expect(execute).not.toHaveBeenCalled();
});
