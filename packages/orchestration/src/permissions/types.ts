/**
 * Permissions 类型（ADR-0011）。
 *
 * 三态语义：
 *
 * - ``allow``：直接执行
 * - ``ask``：走 Risk Agent / 用户审批后才执行（D-8a 未接入审批 UI，临时按 deny 处理）
 * - ``deny``：直接拒绝
 *
 * 匹配优先级（ADR-0011 §三态语义）：**deny > allow > ask > defaultMode**。
 */

export type Decision = "allow" | "ask" | "deny";

/** Predicate 单条件。 */
export type PredicateCondition = {
  field: string;
  op: "<" | "<=" | ">" | ">=" | "==" | "!=" | "in" | "not in";
  /** number | string | array（``in`` 用） */
  value: number | string | (number | string)[];
};

/** Predicate AST：单条件 AND 组合（MVP 仅 AND；ADR-0011 §matcher 语法）。 */
export type Predicate = {
  conditions: PredicateCondition[];
};

/** 解析过的规则。 */
export type ParsedRule = {
  /** tool 名 pattern（精确 / ``.*`` 前缀 / ``*``） */
  toolPattern: string;
  /** 可选参数 predicate；null 表示无 predicate（仅匹配 tool 名） */
  predicate: Predicate | null;
  /** allow / ask / deny */
  decision: Decision;
  /** 规则原始字符串（debug / 审计用） */
  source: string;
};

/** 配置：可以从 JS object 或 YAML 加载。 */
export type PermissionConfig = {
  /** 没规则命中时的默认决策 */
  defaultMode: Decision;
  allow: string[];
  ask: string[];
  deny: string[];
};

/** Engine 给出的最终决策 + 元数据。 */
export type AuthorizeResult = {
  decision: Decision;
  /** 命中的规则原文（None 表示走 defaultMode） */
  matchedRule: string | null;
  /** 人类可读理由 —— 给 LLM / UI 展示 */
  reason: string;
};
