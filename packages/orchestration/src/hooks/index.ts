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
