import { describe, expect, it } from "vitest";

import { inferToolResultState } from "./tool-states";

describe("inferToolResultState", () => {
  it("recognizes an actionable approval envelope", () => {
    expect(inferToolResultState(JSON.stringify({
      isError: true,
      requiresApproval: true,
      requestId: "approval-1",
    }))).toBe("approval-requested");
  });

  it("keeps ordinary tool failures as errors", () => {
    expect(inferToolResultState(JSON.stringify({ isError: true, error: "boom" })))
      .toBe("output-error");
  });
});
