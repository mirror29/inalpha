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
  type AskApprovalCache,
  defaultAskCache,
} from "../permissions/ask-cache.js";
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
  /**
   * 可选挂起池（D-9.1b / ADR-0018）。permissionResolver=ask 时把请求挂进去，供
   * CLI / Web 入口（GET /permissions/pending）查看。缺省用模块级单例（同
   * ``mastra/index.ts`` 注册 HTTP routes 用的 store）。测试可注入 fresh 实例隔离。
   */
  pendingApprovals?: PendingApprovalsStore;
  /** ask 路径 store 超时毫秒数；缺省 30_000（30 秒）。0 / 负数视作默认。 */
  askTimeoutMs?: number;
  /**
   * 可选 session-scoped 短期通行池（D-9.1b 修订）。让"第一次 ask → 用户在 chat
   * 说允许 → agent 重调同 tool 同 input → 放行"在不引入 token 的前提下走通。
   * 缺省用模块级单例；测试可注入 fresh 实例。
   */
  askCache?: AskApprovalCache;
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
      // 兜底 try/catch —— hook runner / permission resolver 自身抛错时不该把异常
      // 冒到 Mastra 上层（review B16）。本文件头注释承诺过 "hook 异常一律不抛出
      // 到上层"，旧实现没真兜住 PreToolUse 阶段的异常。
      const toolName = tool.id;
      try {
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
          // D-9.1b 修订 · session-scoped 短期通行池：
          //
          // 第一次 ask：cache 没命中 → mark + 返 requiresApproval；agent 在 chat
          //   里向用户说明 + 等用户口头同意。
          // 第二次 ask（同 sessionId + 同 toolName + 同 input，60s 内）：cache 命中
          //   → 消费 + 继续走 execute；agent 无需在 input 里塞任何 token。
          //
          // 这一改解决了 Mastra dev playground 没有 popup UI 时"用户在 chat 里说
          // '允许' / agent 重调还是被拦"的死循环。
          //
          // 安全模型：agent 是否真等了用户回复完全靠 prompt 纪律 + 后端硬校验
          // （如 promote 的 fitness > baseline）作护栏；本缓存只解决 UX 死循环，
          // 不是强制审批门。详见 permissions/ask-cache.ts。
          //
          // 同时把请求挂进 PendingApprovalsStore（fire-and-forget）—— GET
          // /permissions/pending 仍能列出来，为未来 CLI / Web 入口保留。
          const cache = opts.askCache ?? defaultAskCache;
          const store = opts.pendingApprovals ?? defaultPendingApprovals;
          const timeoutMs =
            opts.askTimeoutMs && opts.askTimeoutMs > 0 ? opts.askTimeoutMs : undefined;

          if (cache.consume(sessionId, toolName, effectiveInput)) {
            // 第二次 ask 命中 cache → 一次性消费 + 放行（继续往下走 execute）
          } else {
            // 第一次 ask：mark cache + 挂 store（CLI 入口可见）+ 返 requiresApproval
            cache.mark(sessionId, toolName, effectiveInput);
            void store.request({
              toolName,
              toolInput: effectiveInput,
              sessionId,
              timeoutMs,
            });
            return {
              isError: true,
              deniedBy: "permission-ask",
              requiresApproval: true,
              toolName,
              toolInput: effectiveInput,
              message:
                `Tool "${toolName}" requires explicit user approval (permission policy=ask).\n\n` +
                `LLM action guide (reply in the user's language — match their latest message; ` +
                `never hardcode Chinese / English):\n` +
                `  1. Explain the action in plain words. Don't speak the raw tool id; ` +
                `translate it into a user-meaningful phrase (e.g. paper.promote_candidate ` +
                `→ "add this strategy to the live pool" / "把这条策略加入正式策略池"). ` +
                `Include the *why* and key inputs (use human-friendly labels alongside IDs).\n` +
                `  2. Wait for an explicit user confirmation. If they agree, retry the ` +
                `**same tool with the same input** — the system has a 60-second one-shot ` +
                `bypass that will let the retry through. If they refuse / hesitate / ` +
                `give an ambiguous answer, tell them you've cancelled and do NOT retry.\n` +
                `  3. Don't mention tokens, "__approval", or technical bypass details to ` +
                `the user — they just need to say "ok / 允许 / yes" in plain language.`,
            };
          }
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
