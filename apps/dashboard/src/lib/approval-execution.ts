/** Distinguishes direct submission receipts from approvals requiring legacy chat continuation. */
export function approvalExecutionReceipt(value: unknown): { kind: "resume" } | { kind: "submitted"; runId: string } | { kind: "failed" } {
  if (!value || typeof value !== "object") return { kind: "failed" };
  if (!("execution" in value)) return { kind: "resume" };
  const execution = (value as { execution: unknown }).execution;
  if (!execution || typeof execution !== "object") return { kind: "failed" };
  const receipt = execution as { run_id?: unknown; isError?: unknown };
  if (receipt.isError || typeof receipt.run_id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(receipt.run_id)) return { kind: "failed" };
  return { kind: "submitted", runId: receipt.run_id };
}
