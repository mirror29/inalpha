import { describe, expect, it, vi } from "vitest";

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

  it("keeps stop and abort silent, then reports a genuine subsequent failure", () => {
    const onError = vi.fn();
    let stopping = true;
    const subscriber = createChatErrorSubscriber({ isStopping: () => stopping, onError,
      messages: { generic: "Failed", incompleteStream: "Incomplete" } });
    subscriber.onRunFailed({ error: new Error("cancelled") });
    stopping = false;
    subscriber.onRunFailed({ error: new DOMException("aborted", "AbortError") });
    expect(onError).not.toHaveBeenCalled();
    subscriber.onRunErrorEvent({ event: { code: "INCOMPLETE_STREAM" } });
    expect(onError).toHaveBeenCalledWith("Incomplete (INCOMPLETE_STREAM)");
  });
});
