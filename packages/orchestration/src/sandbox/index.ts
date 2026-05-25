/**
 * Sandbox 模块 barrel —— ADR-0020 第二道运行隔离。
 */
export {
  DEFAULT_MAX_OUTPUT_BYTES,
  DEFAULT_SANDBOX_TIMEOUT_MS,
  type SandboxExecuteRequest,
  type SandboxExecuteResult,
  type SandboxLanguage,
  type SandboxProvider,
} from "./provider.js";

export { LocalSubprocessProvider } from "./local.js";

export {
  getSandboxProvider,
  resetSandboxProvider,
  setSandboxProvider,
} from "./factory.js";

export { auditCode, type AuditOptions, type AuditResult } from "./audit.js";

export {
  ContractKindSchema,
  StrategyV1Schema,
  StrategySignalSchema,
  verifyContract,
  type ContractKind,
  type ContractVerifyResult,
  type StrategyV1,
  type StrategySignal,
} from "./contracts.js";
