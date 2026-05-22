/**
 * ``withHooks`` —— Mastra tool execute 中间件。
 *
 * 在 ``allTools`` 里给每个 tool 套一层，把 hook 调用链织进去：
 *
 * ```
 * 1. PreToolUse hook       ← 可改写 input、可 override 权限、可注入 message
 * 2. permission engine     ← （ADR-0011，task #3 接入）
 * 3. tool.execute()        ← 原 tool 调用
 * 4. PostToolUse hook      ← 成功路径：审计 / metric / reconcile
 *    PostToolUseFailure    ← 异常路径：告警 / 归类
 * ```
 *
 * ADR-0010 关键约束：
 *
 * - hook deny **优先级 > permission engine**
 * - blocking hook 失败 / 超时 / deny 时整链 abort，tool 不被调用
 * - hook 注入的 ``message`` 前置到 tool_result（让 LLM 能看到拒绝理由）
 * - PostToolUse ``forceError: true`` 把成功翻成失败（事后验证前移）
 *
 * 实现注：
 *
 * - 对 Mastra 1.x 的 ``createTool()`` 产物只 spread 已知字段（id / description /
 *   inputSchema），其他属性保留原引用。execute 重写为新的 async 函数。
 * - hook 异常一律不抛出到上层 —— 包成 ``{ isError, message }`` 返回，
 *   让 Mastra runtime 把它当 tool 报错处理（LLM 看到错误消息能下一轮决策）。
 * - 现阶段不接 permission engine，仅留 ``permissionResolver`` 参数。task #3 接入。
 */
import type { HookRunner } from "./runner.js";

/**
 * 通用工具 spec —— 不依赖 Mastra 具体导出的 Tool 泛型，确保我们的 wrapper
 * 不被 Mastra 1.x 类型紧耦合（升级时可控）。
 */
type GenericTool = {
  id: string;
  description?: string;
  inputSchema?: unknown;
  outputSchema?: unknown;
  execute?: (input: unknown, ctx?: unknown) => Promise<unknown> | unknown;
  // 允许携带其他厂商字段
  [key: string]: unknown;
};

/**
 * 默认 sessionId 抽取器：按优先级从 Mastra runtime context 取 ID 字段。
 *
 * Mastra 1.x ``ToolExecutionContext`` 会带 ``threadId`` / ``runId`` / ``agentId``
 * （详见 @mastra/core/tools）；本项目自有的 ``requestContext.sessionId`` 作为兜底
 * （任何手动构造的 ctx 走这条）。
 *
 * 优先级：``threadId`` > ``runId`` > ``requestContext.sessionId`` > ``sessionId``
 * （顶层）。命中即停。
 *
 * 这样 audit-log 拿到的 sessionId 既覆盖 Mastra playground 调用，也覆盖测试 / 手
 * 工脚本。
 */
export function defaultGetSessionId(ctx: unknown): string | undefined {
  if (!ctx || typeof ctx !== "object") return undefined;
  const c = ctx as Record<string, unknown>;
  const threadId = pickString(c.threadId);
  if (threadId) return threadId;
  const runId = pickString(c.runId);
  if (runId) return runId;
  const rc = c.requestContext;
  if (rc && typeof rc === "object") {
    const sid = pickString((rc as Record<string, unknown>).sessionId);
    if (sid) return sid;
  }
  const sid = pickString(c.sessionId);
  if (sid) return sid;
  return undefined;
}

function pickString(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

/**
 * 可选的权限解析器（task #3 / ADR-0011 接入）。返回值 deny / ask 走对应路径。
 *
 * 现阶段不传时视作"allow"，所有 tool 都直接执行。
 */
export type PermissionResolver = (
  toolName: string,
  input: unknown,
) => Promise<"allow" | "ask" | "deny"> | "allow" | "ask" | "deny";

export type WithHooksOptions = {
  runner: HookRunner;
  /** 可选权限解析器（task #3 接入）；缺省视为 allow。 */
  permissionResolver?: PermissionResolver;
  /** 可选 sessionId 提供器；从 tool ctx 中取（依赖 Mastra runtime context） */
  getSessionId?: (toolCtx: unknown) => string | undefined;
};

/**
 * 给单个 tool 套上 hooks 中间件。
 *
 * 返回的 tool 与原 tool 是**结构兼容**的（id / description / inputSchema 都保留），
 * 只是 execute 被替换。Mastra agent 注册时把 wrapped tool 当原 tool 用即可。
 */
export function withHooks<T extends GenericTool>(tool: T, opts: WithHooksOptions): T {
  const original = tool.execute;
  if (typeof original !== "function") {
    // 没有 execute 的 tool（例如只声明 schema 的 stub）原样返回
    return tool;
  }

  const getSessionId = opts.getSessionId ?? defaultGetSessionId;

  const wrapped: GenericTool = {
    ...tool,
    execute: async (input: unknown, ctx?: unknown) => {
      const toolName = tool.id;
      const sessionId = getSessionId(ctx);

      // 1. PreToolUse
      const pre = await opts.runner.run("PreToolUse", {
        toolName,
        toolInput: input,
        sessionId,
      });

      if (pre.permissionOverride === "deny") {
        return {
          isError: true,
          message: pre.message ?? `tool ${toolName} blocked by hook`,
          deniedBy: "hook",
          appliedHookIds: pre.appliedHookIds,
        };
      }

      const effectiveInput = pre.updatedInput !== undefined ? pre.updatedInput : input;

      // 2. permission engine（hook 没 override 时才查）
      let permDecision: "allow" | "ask" | "deny" = pre.permissionOverride ?? "allow";
      if (!pre.permissionOverride && opts.permissionResolver) {
        permDecision = await opts.permissionResolver(toolName, effectiveInput);
      }

      if (permDecision === "deny") {
        return {
          isError: true,
          message: `tool ${toolName} denied by permission engine`,
          deniedBy: "permission",
        };
      }

      if (permDecision === "ask") {
        // D-8 阶段：没有 ask 实现（用户审批 / Risk Agent 路径，task #5 起接入）。
        // 暂时把 ask 当 deny + 提示信息，让 LLM 知道这条路需要人工。
        return {
          isError: true,
          message: `tool ${toolName} requires approval (ask path not yet wired; see ADR-0011)`,
          deniedBy: "permission-ask-pending",
        };
      }

      // 3. execute
      let output: unknown;
      let isError = false;
      try {
        output = await original(effectiveInput, ctx);
      } catch (err) {
        output = formatToolError(err);
        isError = true;
      }

      // 4. PostToolUse (success) / PostToolUseFailure (error)
      const postEvent = isError ? "PostToolUseFailure" : "PostToolUse";
      const post = await opts.runner.run(postEvent, {
        toolName,
        toolInput: effectiveInput,
        toolOutput: output,
        isError,
        sessionId,
      });

      if (post.forceError) {
        isError = true;
      }

      // 把 hook 的 message 前置到 tool_result，让 LLM 能读到
      const finalMessage = combineMessages(pre.message, post.message);
      if (finalMessage) {
        output = prependMessage(output, finalMessage, isError);
      }

      // 错误路径统一加 isError 标记（不破坏成功路径原 output 结构）
      if (isError) {
        return { isError: true, output };
      }
      return output;
    },
  };

  return wrapped as T;
}

function formatToolError(err: unknown): { message: string; cause?: unknown } {
  if (err instanceof Error) {
    return { message: err.message, cause: err };
  }
  return { message: String(err) };
}

function combineMessages(...msgs: (string | undefined)[]): string | undefined {
  const present = msgs.filter((m): m is string => Boolean(m));
  if (present.length === 0) return undefined;
  return present.join("\n");
}

function prependMessage(output: unknown, message: string, _isError: boolean): unknown {
  // 字符串 output：直接拼接
  if (typeof output === "string") {
    return `${message}\n${output}`;
  }
  // dict-like output：加一个 ``hookMessage`` 字段而不是替换原结构
  if (output && typeof output === "object") {
    return { ...output, hookMessage: message };
  }
  // 其他类型：包装
  return { hookMessage: message, value: output };
}
