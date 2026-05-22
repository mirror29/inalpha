/**
 * Hooks 类型定义（ADR-0010）。
 *
 * Hook = 编排层的"非 LLM 决策、确定性、可单测"生命周期拦截器。叠在 Mastra tool
 * execute 之外，用来集中实现风控前置 / 审计 log / 环境注入 / 失败告警这类
 * 副作用与硬规则。
 *
 * 设计要点：
 *
 * - 5 + 1 个事件：``SessionStart`` / ``UserPromptSubmit`` / ``PreToolUse`` /
 *   ``PostToolUse`` / ``PostToolUseFailure`` / ``Stop``（Stop 是 ADR 主体后补丁）
 * - ``HookDecision`` 是 superset：能改写 input、能 override permission、能注入
 *   message、能把 success 翻成 failure
 * - 每个 hook 单独配 ``blocking`` 与 ``timeoutMs``（默认 5000ms）
 *
 * 见 ``docs/decisions/0010-orchestration-hooks.md``。
 */

/** Hook 触发的 6 类事件。 */
export type HookEvent =
  | "SessionStart"
  | "UserPromptSubmit"
  | "PreToolUse"
  | "PostToolUse"
  | "PostToolUseFailure"
  | "Stop";

/**
 * 传给 hook handler 的上下文。
 *
 * - ``event`` 永远有
 * - ``toolName`` / ``toolInput`` 在 ``PreToolUse`` 起就有
 * - ``toolOutput`` / ``isError`` 在 ``PostToolUse`` / ``PostToolUseFailure`` 才有
 * - ``sessionId`` 由编排层注入（D-9 起对接 Mastra session；现阶段可空）
 */
export type HookContext = {
  event: HookEvent;
  sessionId?: string;
  toolName?: string;
  toolInput?: unknown;
  toolOutput?: unknown;
  isError?: boolean;
  /** 任意附加 metadata，给特定事件用（如 Stop 用 ``agent`` 字段路由） */
  metadata?: Record<string, unknown>;
};

/**
 * Hook handler 可以返回的决策。
 *
 * **全部 optional**：handler 不需要做决策时可以直接 return（或 return undefined），
 * 编排层视作 "继续"。
 */
export type HookDecision = {
  /** 覆盖 permission engine 的判定（hook 优先级 > permission，ADR-0010 §关键约束 1） */
  permissionOverride?: "allow" | "deny" | "ask";
  /** 改写 tool 的 input（仅 ``PreToolUse`` 生效，其他事件忽略） */
  updatedInput?: unknown;
  /** 注入到 tool_result 前面的文本 */
  message?: string;
  /** 把成功翻成失败（仅 ``PostToolUse`` 生效） */
  forceError?: boolean;
  /** 注入到 system context（仅 ``SessionStart`` 生效） */
  additionalContext?: string;
  /** ``Stop`` hook：``continue: false`` + ``reason`` 强制 LLM 再 turn 一次 */
  continue?: boolean;
  /** ``Stop`` hook 强制 continue 的人类可读理由 */
  reason?: string;
};

export type HookHandler = (ctx: HookContext) => Promise<HookDecision | void> | HookDecision | void;

/** 单个 hook 的注册项。 */
export type HookRegistration = {
  /** 唯一 id（便于 debug / 关闭单个 hook） */
  id: string;
  /** 触发事件 */
  event: HookEvent;
  /**
   * tool 名匹配（仅 ``PreToolUse`` / ``PostToolUse`` / ``PostToolUseFailure``
   * 用 toolName 的事件需要）。
   *
   * 语法：
   *
   * - 精确：``"paper.run_backtest"``
   * - 前缀：``"paper.*"``
   * - OR：``"paper.* | data.*"``（用 ``|`` 分隔，前后空格可选）
   * - 缺省：匹配所有（``SessionStart`` 这类不带 tool 的事件用）
   */
  matcher?: string;
  /** handler 函数 */
  handler: HookHandler;
  /** blocking=true 时 handler 失败 / deny 直接中断；false 只 log（默认 true） */
  blocking?: boolean;
  /** handler 超时（默认 5000ms） */
  timeoutMs?: number;
};

/** 合并多个 handler 的决策（``PreToolUse`` 可能有多个 hook 命中）。 */
export type MergedDecision = HookDecision & {
  /** 哪些 hook 参与了决策（debug 用） */
  appliedHookIds: string[];
};
