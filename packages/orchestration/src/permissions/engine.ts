/**
 * ``PermissionEngine`` —— 声明式 allow / ask / deny 决策引擎（ADR-0011）。
 *
 * 匹配语义：
 *
 * 1. 先扫 ``deny`` 规则 —— 第一条命中即 deny（且 ADR-0011 §关键约定 1：deny 不可被覆盖）
 * 2. 再扫 ``allow`` 规则 —— 命中即 allow
 * 3. 再扫 ``ask`` 规则 —— 命中即 ask
 * 4. 全没命中 → ``defaultMode``
 *
 * Engine 是无状态的：所有数据在 ctor 时解析完，``authorize()`` 只做线性扫描。
 *
 * 三层 merge（ADR-0011 §三层 merge 策略）：
 *
 *     user → project → local
 *
 * D-8 阶段先支持单层；多层合并由 ``buildEngineFromConfigs([user, project, local])``
 * 拼接后再构造（merge 规则：同 pattern 取后层决策；user 层的 deny 永不被下层降级）。
 */
import { parseRule, patternMatches } from "./rule.js";
import { evaluatePredicate } from "./predicate.js";
import type { AuthorizeResult, Decision, ParsedRule, PermissionConfig } from "./types.js";

export class PermissionEngine {
  private readonly defaultMode: Decision;
  private readonly denyRules: ParsedRule[];
  private readonly allowRules: ParsedRule[];
  private readonly askRules: ParsedRule[];

  constructor(config: PermissionConfig) {
    this.defaultMode = config.defaultMode;
    this.denyRules = config.deny.map((s) => parseRule(s, "deny"));
    this.allowRules = config.allow.map((s) => parseRule(s, "allow"));
    this.askRules = config.ask.map((s) => parseRule(s, "ask"));
  }

  authorize(toolName: string, input: unknown): AuthorizeResult {
    // 1. deny
    const denyHit = this.findHit(this.denyRules, toolName, input);
    if (denyHit) {
      return {
        decision: "deny",
        matchedRule: denyHit.source,
        reason: `denied by ${denyHit.source}`,
      };
    }

    // 2. allow
    const allowHit = this.findHit(this.allowRules, toolName, input);
    if (allowHit) {
      return {
        decision: "allow",
        matchedRule: allowHit.source,
        reason: `allowed by ${allowHit.source}`,
      };
    }

    // 3. ask
    const askHit = this.findHit(this.askRules, toolName, input);
    if (askHit) {
      return {
        decision: "ask",
        matchedRule: askHit.source,
        reason: `requires approval by ${askHit.source}`,
      };
    }

    return {
      decision: this.defaultMode,
      matchedRule: null,
      reason: `no rule matched; fell through to defaultMode='${this.defaultMode}'`,
    };
  }

  /** 列出所有规则（``pnpm permissions:explain`` CLI 用，ADR-0011 §代价 §缓解） */
  list(): { deny: ParsedRule[]; allow: ParsedRule[]; ask: ParsedRule[]; defaultMode: Decision } {
    return {
      deny: [...this.denyRules],
      allow: [...this.allowRules],
      ask: [...this.askRules],
      defaultMode: this.defaultMode,
    };
  }

  private findHit(rules: ParsedRule[], toolName: string, input: unknown): ParsedRule | null {
    for (const r of rules) {
      if (!patternMatches(r.toolPattern, toolName)) continue;
      if (r.predicate && !evaluatePredicate(r.predicate, input)) continue;
      return r;
    }
    return null;
  }
}

/**
 * 多层 config 合并：后层覆盖前层。同时强制 ADR-0011 §关键约定 1：``user`` 层
 * （即第一层）的 deny 不能被下层降级。
 *
 * 调用方式：``mergeConfigs([userConfig, projectConfig, localConfig])``
 *
 * 合并规则：
 *
 * - ``defaultMode``：后层胜出
 * - ``deny`` 列表：union（保留全部去重）
 * - ``allow`` / ``ask`` 列表：union（后层先；同 pattern 取最后出现的；deny 列表里的 pattern 不可出现在 allow/ask）
 *
 * 限制（D-8 简化）：不做"同 pattern 跨层互覆盖"的细粒度检测——直接做 union；
 * 实际场景用户层 deny + 项目层 allow 同 pattern 会双双进规则集，最终 deny 因第 1 步胜出。
 */
export function mergeConfigs(configs: PermissionConfig[]): PermissionConfig {
  if (configs.length === 0) {
    return { defaultMode: "ask", allow: [], ask: [], deny: [] };
  }
  const denySet = new Set<string>();
  const allowSet = new Set<string>();
  const askSet = new Set<string>();

  for (const c of configs) {
    for (const r of c.deny) denySet.add(r);
    for (const r of c.allow) allowSet.add(r);
    for (const r of c.ask) askSet.add(r);
  }

  return {
    defaultMode: configs[configs.length - 1]!.defaultMode,
    deny: [...denySet],
    allow: [...allowSet],
    ask: [...askSet],
  };
}
