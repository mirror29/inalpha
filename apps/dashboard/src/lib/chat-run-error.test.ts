import { describe, expect, it } from "vitest";

import { formatChatRunError } from "./chat-run-error";

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
