import { createRequire } from "node:module";
import { describe, expect, it } from "vitest";
import { compactChatRequest } from "./chat-request-context";

const coreRequire = createRequire(createRequire(import.meta.url).resolve("@copilotkit/react-core"));
const { ProxiedCopilotRuntimeAgent } = coreRequire("@copilotkit/core");

describe("persisted chat request context", () => {
  it.each(["agent/run", "agent/connect"])("compacts the installed SDK's %s envelope without mutating history", (method) => {
    const archive = [
      { id: "old-user", role: "user", content: "old research" },
      { id: "old-call", role: "assistant", toolCalls: [{ id: "old-tool", type: "function", function: { name: "research", arguments: "{}" } }] },
      { id: "old-result", role: "tool", toolCallId: "old-tool", content: "x".repeat(8_500_000) },
    ];
    const current = [
      { id: "current-user", role: "user", content: "continue" },
      { id: "current-call", role: "assistant", toolCalls: [{ id: "approval-tool", type: "function", function: { name: "approve", arguments: '{"planId":"p"}' } }] },
      { id: "current-result", role: "tool", toolCallId: "approval-tool", content: '{"approved":true}' },
    ];
    const agent = new ProxiedCopilotRuntimeAgent({ agentId: "orchestrator", runtimeUrl: "/api/copilotkit", transport: "single" });
    agent.setMessages([...archive, ...current]);
    const input = { threadId: "owner-thread", runId: "run", state: {}, messages: agent.messages, tools: [], context: [], forwardedProps: { approval: "metadata" } };
    const original = agent.createSingleRouteRequestInit(input, method, { agentId: "orchestrator" });
    const compact = compactChatRequest(original)!;
    const sent = JSON.parse(compact.body as string);
    expect(sent.body.messages).toEqual(current);
    expect(sent.body).toEqual({ ...input, messages: current });
    expect(sent.method).toBe(method);
    expect(sent.params).toEqual({ agentId: "orchestrator" });
    expect((compact.body as string).length).toBeLessThan(1_000);
    expect((original.body as string).length).toBeGreaterThan(8_500_000);
    expect(agent.messages).toEqual([...archive, ...current]);
    expect(compact.headers).toBe(original.headers);
  });

  it.each([
    { method: "info" },
    { method: "agent/run", params: { agentId: "other" }, body: { threadId: "t", messages: [{ role: "user" }, { role: "user" }] } },
    { method: "agent/run", params: { agentId: "orchestrator" }, body: { messages: [{ role: "user" }, { role: "user" }] } },
    { method: "agent/run", params: { agentId: "orchestrator" }, body: { threadId: "t", messages: [{ role: "assistant" }, { role: "tool" }] } },
    { method: "agent/run", params: { agentId: "orchestrator" }, body: { threadId: "t", messages: [{ role: "user" }, { role: "assistant" }] } },
  ])("passes through unsupported or already current-turn requests", (envelope) => {
    const init = { body: JSON.stringify(envelope) };
    expect(compactChatRequest(init)).toBe(init);
  });

  it("preserves malformed/non-string bodies and fetch cancellation", () => {
    const signal = new AbortController().signal;
    const init = { body: "{broken", signal };
    expect(compactChatRequest(init)).toBe(init);
    expect(compactChatRequest({ body: new FormData(), signal })?.signal).toBe(signal);
    expect(compactChatRequest()).toBeUndefined();
  });

  it("keeps an earlier declaring tool turn when an approval result follows a new user message", () => {
    const messages = [
      { role: "user", content: "old archive" },
      { role: "assistant", content: "old response" },
      { role: "user", content: "prepare approval" },
      { role: "assistant", toolCalls: [{ id: "approval" }] },
      { role: "user", content: "approved" },
      { role: "tool", toolCallId: "approval", content: "ok" },
    ];
    const init = { body: JSON.stringify({ method: "agent/run", params: { agentId: "orchestrator" }, body: { threadId: "t", messages } }) };
    expect(JSON.parse(compactChatRequest(init)!.body as string).body.messages).toEqual(messages.slice(2));
    messages[5].toolCallId = "unknown";
    const orphan = { body: JSON.stringify({ method: "agent/run", params: { agentId: "orchestrator" }, body: { threadId: "t", messages } }) };
    expect(compactChatRequest(orphan)).toBe(orphan);
  });
});
