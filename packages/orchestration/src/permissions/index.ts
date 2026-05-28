/**
 * Permissions 层导出（ADR-0011）。
 */
export type {
  AuthorizeResult,
  Decision,
  ParsedRule,
  PermissionConfig,
  Predicate,
  PredicateCondition,
} from "./types.js";

export { PermissionEngine, mergeConfigs } from "./engine.js";
export { parseRule, patternMatches } from "./rule.js";
export { parsePredicate, evaluatePredicate } from "./predicate.js";
export { DEFAULT_PERMISSIONS } from "./defaults.js";
export {
  loadPermissionConfigFromFile,
  loadDefaultPermissions,
  resolveDefaultYamlPath,
} from "./yaml_loader.js";
export { PermissionConfigSchema } from "./schema.js";
