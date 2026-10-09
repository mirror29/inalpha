import { describe, expect, it } from "vitest";
import { approvalExecutionReceipt } from "./approval-execution";

describe("direct approval receipts", () => {
  it("routes a real submitted task without another model prompt", () => {
    expect(approvalExecutionReceipt({ execution: { run_id: "680ac835-ef1d-4f72-a298-76ca1ba4c81e", status: "queued" } })).toEqual({ kind: "submitted", runId: "680ac835-ef1d-4f72-a298-76ca1ba4c81e" });
  });
  it("keeps legacy approval continuation for other tools", () => {
    expect(approvalExecutionReceipt({ ok: true })).toEqual({ kind: "resume" });
  });
  it("never retries a failed direct task through a model turn", () => {
    for (const execution of [null, {}, { isError: true, run_id: "run" }, { run_id: "javascript:invalid" }]) {
      expect(approvalExecutionReceipt({ execution })).toEqual({ kind: "failed" });
    }
  });
});
