import { createRequire } from "node:module";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createChatErrorSubscriber, formatChatRunError } from "./chat-run-error";

describe("formatChatRunError", () => {
  const messages = {
    generic: "generic failure",
    incompleteStream: "check provider balance, quota, or API key",
  };

  it("turns an incomplete stream into actionable provider guidance", () => {
    expect(formatChatRunError({ code: "INCOMPLETE_STREAM" }, messages)).toBe(
      "check provider balance, quota, or API key (INCOMPLETE_STREAM)",
    );
  });

  it("preserves a useful upstream message", () => {
    expect(
      formatChatRunError(
        { code: "PROVIDER_ERROR", message: "Insufficient Balance" },
        messages,
      ),
    ).toBe("Insufficient Balance (PROVIDER_ERROR)");
  });

  it("falls back to the generic message for opaque errors", () => {
    expect(formatChatRunError({ message: "[object Object]" }, messages)).toBe(
      "generic failure",
    );
  });
});


describe("chat failure subscriber", () => {
  it("shows a rejected HTTP run without requiring a RUN_ERROR event", () => {
    const onError = vi.fn();
    const subscriber = createChatErrorSubscriber({ isStopping: () => false, onError,
      messages: { generic: "Request failed; retry", incompleteStream: "Incomplete" } });
    subscriber.onRunFailed({ error: new Error("HTTP 400: private request details") });
    expect(onError).toHaveBeenCalledWith("Request failed; retry (CHAT_REQUEST_FAILED)");
  });

  it("silences requested stops and reports unexpected aborts", () => {
    const onError = vi.fn();
    let stopping = true;
    const subscriber = createChatErrorSubscriber({ isStopping: () => stopping, onError,
      messages: { generic: "Failed", incompleteStream: "Incomplete" } });
    subscriber.onRunFailed({ error: new Error("cancelled") });
    stopping = false;
    expect(onError).not.toHaveBeenCalled();
    subscriber.onRunFailed({ error: new DOMException("unexpected timeout abort", "AbortError") });
    expect(onError).toHaveBeenLastCalledWith("Failed (CHAT_REQUEST_FAILED)");
    subscriber.onRunErrorEvent({ event: { code: "INCOMPLETE_STREAM" } });
    expect(onError).toHaveBeenCalledWith("Incomplete (INCOMPLETE_STREAM)");
  });
});


/** Load the real client used by the installed adapter without duplicating its dependency. */
const adapterRequire = createRequire(createRequire(import.meta.url).resolve("@ag-ui/mastra"));
const { HttpAgent } = adapterRequire("@ag-ui/client");
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("AG-UI failure lifecycle", () => {
  it("subscribed HttpAgent reports HTTP 400 and ends its running state", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Invalid request body", { status: 400 })));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const onError = vi.fn();
    const agent = new HttpAgent({ url: "http://chat.test/run", threadId: "test-thread",
      initialMessages: [{ id: "user-1", role: "user", content: "Continue" }] });
    agent.subscribe(createChatErrorSubscriber({ isStopping: () => false, onError,
      messages: { generic: "Request failed", incompleteStream: "Incomplete" } }));
    await expect(agent.runAgent()).rejects.toBeDefined();
    expect(onError).toHaveBeenCalledWith("Request failed (CHAT_REQUEST_FAILED)");
    expect(agent.isRunning).toBe(false);
  });

  it("real HttpAgent resets a specific protocol error before a later HTTP failure", async () => {
    const events = [
      { type: "RUN_STARTED", threadId: "test-thread", runId: "run-1" },
      { type: "RUN_ERROR", message: "Interrupted", code: "INCOMPLETE_STREAM" },
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(events, { headers: { "Content-Type": "text/event-stream" } }))
      .mockResolvedValueOnce(new Response("Invalid request body", { status: 400 })));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const onError = vi.fn();
    const agent = new HttpAgent({ url: "http://chat.test/run", threadId: "test-thread" });
    agent.subscribe(createChatErrorSubscriber({ isStopping: () => false, onError,
      messages: { generic: "Failed", incompleteStream: "Incomplete" } }));
    await agent.runAgent({ runId: "run-1" }).catch(() => {});
    expect(onError).toHaveBeenLastCalledWith("Interrupted (INCOMPLETE_STREAM)");
    await expect(agent.runAgent({ runId: "run-2" })).rejects.toBeDefined();
    expect(onError).toHaveBeenLastCalledWith("Failed (CHAT_REQUEST_FAILED)");
    expect(agent.isRunning).toBe(false);
  });

  it("preserves specific errors and resets precedence for the next run", () => {
    const onError = vi.fn();
    const subscriber = createChatErrorSubscriber({ isStopping: () => false, onError,
      messages: { generic: "Failed", incompleteStream: "Incomplete" } });
    subscriber.onRunInitialized();
    subscriber.onRunErrorEvent({ event: { code: "INCOMPLETE_STREAM" } });
    subscriber.onRunFailed({ error: new Error("connection reset") });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenLastCalledWith("Incomplete (INCOMPLETE_STREAM)");
    subscriber.onRunInitialized();
    subscriber.onRunFailed({ error: new Error("connection reset") });
    expect(onError).toHaveBeenLastCalledWith("Failed (CHAT_REQUEST_FAILED)");
  });
});
