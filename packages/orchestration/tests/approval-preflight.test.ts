import { describe, expect, it, vi } from "vitest";
import { HookRunner } from "../src/hooks/runner.js";
import { withHooks } from "../src/hooks/with-hooks.js";
import type { PendingApprovalsStore } from "../src/permissions/pending.js";

/** Preparation must precede approval consumption and never execute after a failed check. */
describe("approval preparation ordering", () => {
  it("rejects invalid preparation before requesting or consuming an approval", async () => {
    const execute = vi.fn();
    const request = vi.fn();
    const consumeApproved = vi.fn();
    const tool = withHooks({ id: "test.costful", execute }, {
      runner: new HookRunner(),
      permissionResolver: () => "ask",
      getAuthSub: () => "owner",
      getSessionId: () => "thread",
      pendingApprovals: { request, consumeApproved } as unknown as PendingApprovalsStore,
      approvalPreflight: async () => { throw new Error("EVOLUTION_DATA_GAP_INVALID"); },
    });
    expect(await tool.execute({})).toMatchObject({ isError: true, deniedBy: "preflight" });
    expect(request).not.toHaveBeenCalled();
    expect(consumeApproved).not.toHaveBeenCalled();
    expect(execute).not.toHaveBeenCalled();
  });
  it("does not perform preparation for denied tools", async () => {
    const approvalPreflight = vi.fn();
    const tool = withHooks({ id: "test.costful", execute: vi.fn() }, {
      runner: new HookRunner(), permissionResolver: () => "deny", approvalPreflight,
    });
    await tool.execute({});
    expect(approvalPreflight).not.toHaveBeenCalled();
  });
});

/** Approval identity and execution must use the server-prepared input, not model-provided hashes. */
it("projects prepared input into approval and executes that exact input", async () => {
  const preparedInput = { seedStrategyId: "candidate:owned", preparation: { seed_source_hash: "verified" } };
  const consumeApproved = vi.fn().mockResolvedValue("operation");
  const execute = vi.fn().mockResolvedValue({ run_id: "run" });
  const tool = withHooks({ id: "test.costful", execute }, {
    runner: new HookRunner(), permissionResolver: () => "ask",
    getAuthSub: () => "owner", getSessionId: () => "thread",
    pendingApprovals: { consumeApproved } as unknown as PendingApprovalsStore,
    approvalPreflight: async () => ({ input: preparedInput }),
  });
  await tool.execute({ preparation: "untrusted" });
  expect(consumeApproved).toHaveBeenCalledWith(expect.objectContaining({ approvalInput: preparedInput }));
  expect(execute).toHaveBeenCalledWith(preparedInput, undefined);
});
