/**
 * 把 ``allTools`` 套上 hooks + permissions 后导出，给 ``orchestrator`` agent 用。
 *
 * 这是 ADR-0010 / ADR-0011 / ADR-0012 三个 ADR 的**唯一汇合点**——
 *
 * - **ADR-0010 hooks**：每个 tool 的 execute 前后跑 ``HookRunner.run()``
 *   - PostToolUse 注入 audit log（脱敏后 console.log JSON 行）
 * - **ADR-0011 permissions**：每个 tool 执行前查 ``PermissionEngine.authorize()``
 *   - 默认规则禁直接下单（``paper.submit_order_intent``）和危险全局动作
 *   - hook 决策优先级 > permission（hook 可临时覆盖）
 * - **ADR-0012 plan-exec**：``trade.*`` 五件套允许，``paper.submit_order_intent`` 拒绝
 *   - 这让 LLM 没有"直连下单"路径，必须 createPlan → approve → execute
 *
 * 单元测试见 ``tests/wired-tools.test.ts``：覆盖 trip wires（不能直接下单、能跑 plan/exec）。
 */
import {
  HookRunner,
  defaultAuditRegistration,
  defaultFactorExpressionAuditRegistration,
  defaultGridSizeCapRegistration,
  defaultIdempotencyRegistrations,
  defaultInjectCurrentDateRegistration,
  defaultStrategyCodeAuditRegistration,
  withHooks,
} from "../hooks/index.js";
import type { AskApprovalCache } from "../permissions/ask-cache.js";
import { PermissionEngine, loadDefaultPermissions } from "../permissions/index.js";
import type { Decision } from "../permissions/index.js";
import type { PendingApprovalsStore } from "../permissions/pending.js";
import {
  allTools,
  orchestratorToolList,
  riskTools,
  traderTools,
} from "../tools/index.js";
import { getMcpToolsCached } from "../mcp/index.js";

/** 默认 hook runner —— audit-log + grid-size-cap + tool-idempotency + inject-current-date。 */
function buildDefaultRunner(
  auditSink?: (record: Record<string, unknown>) => void,
): HookRunner {
  const runner = new HookRunner();
  runner.register(defaultAuditRegistration(auditSink));
  runner.register(defaultGridSizeCapRegistration());
  // D-9 fix：SessionStart 注入今天日期，挡 LLM 训练 cutoff 早导致日期猜错
  runner.register(defaultInjectCurrentDateRegistration());
  // D-9 · ADR-0020 E1：自创策略源码 PreToolUse 限长 + 注入串拦截
  runner.register(defaultStrategyCodeAuditRegistration());
  // D-12 · 因子发现 L1：自定义因子表达式外围拦截（负 lag / 未来命名 / 超长）
  runner.register(defaultFactorExpressionAuditRegistration());
  // ADR-0025 follow-up：DeepSeek 在 Mastra agent loop 偶尔 retry 同 swarm 调用
  // 多次（同 input 同输出）；这对 hook 把重复 deny 掉并把上次结果摘要给 LLM
  const idem = defaultIdempotencyRegistrations();
  runner.register(idem.pre);
  runner.register(idem.post);
  return runner;
}

/**
 * 默认 permission engine。
 *
 * 规则来源（ADR-0011 / D-8b · #4）：
 *
 *   ``INALPHA_PERMISSIONS_FILE`` env  →  包内 ``config/permissions.default.yaml``
 *   →  ``DEFAULT_PERMISSIONS`` 常量（仅在默认 yaml 文件缺失时 fallback）
 *
 * env 指定的文件加载失败 / schema 不匹配会 throw——这是 issue #4 验收第 4 条
 * 要求的 fail-fast，不做静默 fallback。
 */
function buildDefaultPermissionEngine(): PermissionEngine {
  return new PermissionEngine(loadDefaultPermissions());
}

export type WireToolsOptions = {
  /** 自定义 hook runner（测试 / 替换默认）；缺省走 ``defaultAuditRegistration`` */
  hookRunner?: HookRunner;
  /** 自定义 permission engine；缺省走 ``loadDefaultPermissions()``（yaml 优先 / 常量兜底） */
  permissionEngine?: PermissionEngine;
  /** audit-log sink（默认 console.log JSON 行）；当传入自定义 hookRunner 时本字段忽略 */
  auditSink?: (record: Record<string, unknown>) => void;
  /** ask 路径挂起池（D-9.1b / ADR-0018）；缺省用模块单例，测试可注入 fresh 实例。 */
  pendingApprovals?: PendingApprovalsStore;
  /** ask 路径超时毫秒数；缺省 30_000（30 秒）。 */
  askTimeoutMs?: number;
  /** session-scoped 短期通行池（D-9.1b 修订）；缺省用模块单例，测试可注入 fresh 实例。 */
  askCache?: AskApprovalCache;
};

/** wireTools 返回的 tool 形态（id 必有，其它字段透传）。 */
export type WiredTool = {
  id: string;
  description?: string;
  execute?: (input: unknown, ctx?: unknown) => Promise<unknown> | unknown;
  [key: string]: unknown;
};

/**
 * 套上 hooks + permissions 后的 tools。
 *
 * 用法：``new Agent({ tools: Object.fromEntries(wireTools().map(t => [t.id, t])) })``
 *
 * 注：返回类型故意放宽到 ``WiredTool[]``——withHooks 会把 Mastra ``Tool<...>`` 的严格泛型
 * 擦除（execute 签名变 ``(unknown, unknown) => unknown``），跟原始 ``typeof allTools[number]``
 * union 已不兼容。但 Mastra ``Agent.tools`` 字段接受任何 id+execute 形状，所以放宽不会
 * 影响 runtime。
 */
/** 把任意 tool 子集套上 hooks + permissions。trader / risk 子 agent 用。 */
export function wireToolList(
  tools: readonly unknown[],
  opts: WireToolsOptions = {},
): WiredTool[] {
  const runner = opts.hookRunner ?? buildDefaultRunner(opts.auditSink);
  const engine = opts.permissionEngine ?? buildDefaultPermissionEngine();

  const resolver = (toolName: string, input: unknown): Decision => {
    return engine.authorize(toolName, input).decision;
  };

  return tools.map((tool) =>
    withHooks(tool as WiredTool, {
      runner,
      permissionResolver: resolver,
      pendingApprovals: opts.pendingApprovals,
      askTimeoutMs: opts.askTimeoutMs,
      askCache: opts.askCache,
    }),
  );
}

export function wireTools(opts: WireToolsOptions = {}): WiredTool[] {
  return wireToolList(allTools, opts);
}

// ────────────────────────────────────────────────────────────────────
// 默认 / 共享实例（**单例**：orchestrator + trader + risk 共用同一个 runner + engine
// → audit log 全局一份，permission deny 全局一致）
// ────────────────────────────────────────────────────────────────────

export const defaultHookRunner = buildDefaultRunner();
export const defaultPermissionEngine = buildDefaultPermissionEngine();

const sharedOpts: WireToolsOptions = {
  hookRunner: defaultHookRunner,
  permissionEngine: defaultPermissionEngine,
};

/** 默认 wired tools（全集），给 ``orchestrator.ts`` 兜底用。 */
export const wiredTools = wireToolList(allTools, sharedOpts);

/** Trader subagent 用的 wrapped 子集。 */
export const wiredTraderTools = wireToolList(traderTools, sharedOpts);

/** Risk subagent 用的 wrapped 子集。 */
export const wiredRiskTools = wireToolList(riskTools, sharedOpts);

/** orchestrator 用的 wrapped 子集（路由层级 tool + research.deep_dive）。 */
export const wiredOrchestratorTools = wireToolList(orchestratorToolList, sharedOpts);

// ────────────────────────────────────────────────────────────────────
// MCP tool（ADR-0009）：可插拔第三方 tool，与内置 tool 走同一套 hooks + permissions。
// ────────────────────────────────────────────────────────────────────

/**
 * 加载 MCP server 暴露的 tool 并套上**同一个**共享 hook runner + permission engine。
 *
 * - 命名 ``mcp__<server>__<verb>``；未显式 allow 的 MCP tool 由 ``defaultMode: ask``
 *   fail-closed 兜底（permissions.default.yaml），只读公开源（如 coingecko）可显式 allow。
 * - 永不抛错（``getMcpToolsCached`` 内部吞错）：MCP 全挂时返回空数组，orchestrator 照常工作。
 * - memoize：进程内只真正连一次 MCP server。
 *
 * @returns 已 wire 的 MCP tool 数组（可能为空）
 */
export async function loadWiredMcpTools(): Promise<WiredTool[]> {
  const rawMcpTools = await getMcpToolsCached();
  if (rawMcpTools.length === 0) return [];
  return wireToolList(rawMcpTools, sharedOpts);
}
