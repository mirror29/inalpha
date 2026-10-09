import { randomUUID } from "node:crypto";
import { expect, it, vi } from "vitest";
import { HookRunner } from "../src/hooks/runner.js";
import { AUTH_SUB_KEY, withHooks } from "../src/hooks/with-hooks.js";
import { registerChatInvocation } from "../src/mastra/llm/chat-invocation-scope.js";
import { USER_LLM_SNAPSHOT_KEY } from "../src/mastra/llm/evolution-snapshot.js";

it("records trusted E2 provenance before execution and fails closed if it cannot persist", async () => {
  const events: string[] = [];
  const context = new Map<string, unknown>([[AUTH_SUB_KEY, "alice"], ["sessionId", "thread"],
    [USER_LLM_SNAPSHOT_KEY, { config_id: "model" }]]);
  const invocation = randomUUID(); registerChatInvocation(context, invocation, "alice");
  const linker = vi.fn(async () => { events.push("link"); });
  const tool = withHooks({ id: "evolver.start_evolution_loop", execute: async () => { events.push("execute"); return { loop_id: "loop" }; } }, {
    runner: new HookRunner(), permissionResolver: () => "allow", chatOperationLinker: linker,
  });
  await tool.execute!({}, { requestContext: context });
  expect(events).toEqual(["link", "execute"]);
  expect(linker).toHaveBeenCalledWith(expect.objectContaining({ invocationId: invocation, authSub: "alice" }));
  linker.mockRejectedValueOnce(new Error("storage unavailable")); events.length = 0;
  expect(await tool.execute!({}, { requestContext: context })).toMatchObject({ deniedBy: "chat-lineage-unavailable" });
  expect(events).toEqual([]);
});

it("never treats a client context field as trusted E2 provenance", async () => {
  const linker = vi.fn();
  const context = new Map<string, unknown>([[AUTH_SUB_KEY, "alice"], ["sessionId", "thread"],
    [USER_LLM_SNAPSHOT_KEY, { config_id: "model" }], ["inalpha__chatInvocationId", randomUUID()]]);
  const tool = withHooks({ id: "evolver.run_event_campaign", execute: async () => ({ campaign_id: "campaign" }) }, {
    runner: new HookRunner(), permissionResolver: () => "allow", chatOperationLinker: linker,
  });
  await tool.execute!({}, { requestContext: context });
  expect(linker).not.toHaveBeenCalled();
});
