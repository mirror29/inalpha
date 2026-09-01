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
import {
  APPROVAL_OPERATION_ID_KEY,
  getRequestContextValue,
  setRequestContextValue,
  USER_LLM_SNAPSHOT_KEY,
  type EvolutionLLMSnapshot,
} from "../mastra/llm/evolution-snapshot.js";
import { projectApprovalInput } from "../permissions/approval-identity.js";
import {
  type PendingApprovalsStore,
  pendingApprovals as defaultPendingApprovals,
} from "../permissions/pending.js";
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

const DURABLE_EVOLUTION_APPROVAL_TOOLS = new Set([
  "evolver.run_evolution",
  "evolver.run_event_campaign",
]);
const E2_CAMPAIGN_RETRY_WINDOW_MS = 2 * 60 * 1_000;

/**
 * mastra ``server.middleware`` 从 Bearer JWT 解出的已认证主体（sub）写进 RequestContext
 * 的 key（#91）。getSessionId 最高优先读它 → askCache 按已认证主体 scope（替代 __global__）。
 * 单租户 = console subject（稳定唯一）；多租户 = 每用户隔离，自动生效。
 */
export const AUTH_SUB_KEY = "inalpha__authSub";

/**
 * 提取 conversation 级稳定 ID；明确忽略每轮变化的 ``runId``。
 *
 * owner 由 ``defaultGetAuthSub`` 独立提取。缺少稳定 thread/session 时，
 * costful ask 必须 fail closed，不能回退到 owner 或全局作用域。
 */
export function defaultGetSessionId(ctx: unknown): string | undefined {
  if (!ctx || typeof ctx !== "object") return undefined;
  const context = ctx as Record<string, unknown>;
  const agent = context.agent;
  if (agent && typeof agent === "object") {
    const threadId = pickString((agent as Record<string, unknown>).threadId);
    if (threadId) return threadId;
  }
  const threadId = pickString(context.threadId);
  if (threadId) return threadId;
  const requestContext = context.requestContext;
  if (requestContext && typeof requestContext === "object") {
    const sessionId = pickString((requestContext as Record<string, unknown>).sessionId);
    if (sessionId) return sessionId;
    const getter = (requestContext as { get?: (key: string) => unknown }).get;
    if (typeof getter === "function") {
      const mappedSessionId = pickString(getter.call(requestContext, "sessionId"));
      if (mappedSessionId) return mappedSessionId;
    }
  }
  return pickString(context.sessionId);
}

/** Extracts the authenticated approval owner injected by identityMiddleware. */
export function defaultGetAuthSub(ctx: unknown): string | undefined {
  if (!ctx || typeof ctx !== "object") return undefined;
  const requestContext = (ctx as Record<string, unknown>).requestContext;
  if (!requestContext || typeof requestContext !== "object") return undefined;
  const getter = (requestContext as { get?: (key: string) => unknown }).get;
  return typeof getter === "function"
    ? pickString(getter.call(requestContext, AUTH_SUB_KEY))
    : pickString((requestContext as Record<string, unknown>)[AUTH_SUB_KEY]);
}

/** Extracts the per-turn run ID for diagnostics; approval never uses it. */
export function defaultGetTurnId(ctx: unknown): string | undefined {
  if (!ctx || typeof ctx !== "object") return undefined;
  return pickString((ctx as Record<string, unknown>).runId);
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
  /** 可选 sessionId 提供器；必须返回稳定 thread/session ID。 */
  getSessionId?: (toolCtx: unknown) => string | undefined;
  /** 可选审批 owner 提供器；缺省读取已验签 Bearer 的 sub。 */
  getAuthSub?: (toolCtx: unknown) => string | undefined;
  /**
   * 可选挂起池（D-9.1b / ADR-0018）。permissionResolver=ask 时把请求挂进去，供
   * CLI / Web 入口（GET /permissions/pending）查看。缺省用模块级单例（同
   * ``mastra/index.ts`` 注册 HTTP routes 用的 store）。测试可注入 fresh 实例隔离。
   */
  pendingApprovals?: PendingApprovalsStore;
  /** ask 路径 store 超时毫秒数；缺省 30_000（30 秒）。0 / 负数视作默认。 */
  askTimeoutMs?: number;
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
  const getAuthSub = opts.getAuthSub ?? defaultGetAuthSub;

  const wrapped: GenericTool = {
    ...tool,
    execute: async (input: unknown, ctx?: unknown) => {
      // 兜底 try/catch —— hook runner / permission resolver 自身抛错时不该把异常
      // 冒到 Mastra 上层（review B16）。本文件头注释承诺过 "hook 异常一律不抛出
      // 到上层"，旧实现没真兜住 PreToolUse 阶段的异常。
      const toolName = tool.id;
      try {
        const sessionId = getSessionId(ctx);
        const authSub = getAuthSub(ctx);

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
          const store = opts.pendingApprovals ?? defaultPendingApprovals;
          const projectedInput = projectApprovalInput(toolName, effectiveInput);
          const durableEvolutionApproval = DURABLE_EVOLUTION_APPROVAL_TOOLS.has(toolName);
          const llmSnapshot = durableEvolutionApproval
            ? getRequestContextValue<EvolutionLLMSnapshot>(ctx, USER_LLM_SNAPSHOT_KEY)
            : undefined;
          const approvalInput = llmSnapshot
            ? { request: projectedInput, llm_snapshot: llmSnapshot }
            : projectedInput;
          if (!authSub || !sessionId || (durableEvolutionApproval && !llmSnapshot)) {
            return {
              isError: true,
              deniedBy: "permission-ask",
              requiresApproval: true,
              toolName,
              toolInput: effectiveInput,
              message:
                `APPROVAL_UNAVAILABLE: tool "${toolName}" requires a verified owner, stable ` +
                `thread/session, and a frozen non-secret LLM configuration. Do not retry or infer ` +
                `consent from chat text. Explain this in the user's latest language.`,
            };
          }

          const operationId = await store.consumeApproved({
            authSub,
            sessionId,
            toolName,
            approvalInput,
            reuseAfterConsume: toolName === "evolver.run_evolution",
            reuseOnceAfterConsumeMs:
              toolName === "evolver.run_event_campaign"
                ? E2_CAMPAIGN_RETRY_WINDOW_MS
                : undefined,
          });
          if (!operationId) {
            const approvalViewInput = llmSnapshot
              ? { request: effectiveInput, llm_snapshot: llmSnapshot }
              : effectiveInput;
            const pending = store.request({
              authSub,
              sessionId,
              toolName,
              toolInput: approvalViewInput,
              approvalInput,
              timeoutMs:
                opts.askTimeoutMs && opts.askTimeoutMs > 0
                  ? opts.askTimeoutMs
                  : durableEvolutionApproval
                    ? 300_000
                    : undefined,
            });
            return {
              isError: true,
              deniedBy: "permission-ask",
              requiresApproval: true,
              requestId: pending.requestId,
              toolName,
              toolInput: approvalViewInput,
              message:
                `APPROVAL_REQUIRED: tool "${toolName}" needs an explicit decision through the ` +
                `trusted approval UI/API. Chat text, a new turn, or model output cannot approve it. ` +
                `Show the purpose, frozen model, estimated cost, and key inputs, then wait. Reply in ` +
                `the user's latest language. If denied or expired, cancel the action.`,
            };
          }
          setRequestContextValue(ctx, APPROVAL_OPERATION_ID_KEY, operationId);
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
      } catch (err) {
        // 最后一道防线：runner / resolver / 内部包装代码意外抛错时兜成 isError result
        return {
          isError: true,
          message: `tool ${toolName} middleware error: ${formatToolError(err).message}`,
          deniedBy: "middleware-error",
        };
      }
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
