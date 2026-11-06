import { describe, expect, it } from "vitest";
import { registerChatInvocation, trustedChatInvocation } from "../src/mastra/llm/chat-invocation-scope.js";
import { PendingApprovalsStore } from "../src/permissions/pending.js";

describe("trusted chat approval provenance", () => {
  it("ignores client fields and rejects another context or owner", () => {
    const context = { inalpha__chatInvocationId: "client" };
    expect(trustedChatInvocation(context, "alice")).toBeUndefined();
    registerChatInvocation(context, "server", "alice");
    expect(trustedChatInvocation(context, "alice")).toBe("server");
    expect(trustedChatInvocation(context, "bob")).toBeUndefined();
    expect(trustedChatInvocation({ ...context }, "alice")).toBeUndefined();
  });

  it("keeps the original preparation invocation when later requests reuse an approval", () => {
    const store = new PendingApprovalsStore(() => {});
    const args = { toolName: "evolver.run_evolution", toolInput: {}, approvalInput: {}, sessionId: "thread", authSub: "alice" };
    try {
      const first = store.request({ ...args, chatInvocationId: "first" });
      const duplicate = store.request({ ...args, chatInvocationId: "second" });
      expect(duplicate.requestId).toBe(first.requestId);
      expect(duplicate.chatInvocationId).toBe("first");
      first.chatInvocationId = "mutated";
      expect(store.list("alice")[0].chatInvocationId).toBe("first");
    } finally { store.clearAll(); }
  });
});
