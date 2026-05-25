/**
 * Hooks 层导出（ADR-0010）。
 */
export type {
  HookContext,
  HookDecision,
  HookEvent,
  HookHandler,
  HookRegistration,
  MergedDecision,
} from "./types.js";

export { HookRunner } from "./runner.js";
export { toolMatches } from "./matcher.js";
export { withHooks, defaultGetSessionId } from "./with-hooks.js";
export type { PermissionResolver, WithHooksOptions } from "./with-hooks.js";

export { createAuditLogHandler, defaultAuditRegistration } from "./handlers/audit-log.js";
export {
  DEFAULT_GRID_MAX,
  createGridSizeCapHandler,
  defaultGridSizeCapRegistration,
} from "./handlers/grid-size-cap.js";

// Stop hook handlers（ADR-0010 §Stop hook 补丁）
export { createPendingPlanCheckHandler } from "./handlers/pending-plan-check.js";
export type {
  PendingPlanLite,
  PendingPlanFetcher,
  PendingPlanCheckOptions,
} from "./handlers/pending-plan-check.js";
export { createFillReconcileCheckHandler } from "./handlers/fill-reconcile-check.js";
export type {
  UnreconciledOrderLite,
  UnreconciledFetcher,
  FillReconcileCheckOptions,
} from "./handlers/fill-reconcile-check.js";
export { createAnalystQuorumCheckHandler } from "./handlers/analyst-quorum-check.js";
export type {
  AnalystBriefLite,
  LastResearchFetcher,
  AnalystQuorumCheckOptions,
} from "./handlers/analyst-quorum-check.js";

export { StopHookRunner, formatStopNotice } from "./stop-runner.js";
export type { StopDecision } from "./stop-runner.js";
