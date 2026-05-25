/**
 * ``pending-plan-check`` —— Stop hook handler。
 *
 * 用途（ADR-0010 §Stop hook 补丁场景 1）：trader / orchestrator 调完
 * ``trade.create_plan`` 后想结束 turn 但 plan 还在 ``pending_approval`` / ``approved``。
 * 这是**没干完活**——用户没看到执行结果，体感是"我让它下单它没下"。
 *
 * 实现：
 *
 * - 入参：plan 列表 fetcher（注入式，便于测试 + 解耦 mastra/paper 依赖）
 *   - **生产路径**：注入一个调 ``paper.list_plans({ status: 'approved' or 'pending_approval' })`` 的 fetcher
 *   - **测试路径**：注入一个返回固定 plan 数组的 fetcher
 * - 命中条件：本 turn / session 内有 ≥ 1 个未 executed 的 plan
 * - 返回：``{ continue: false, reason: "..." }`` 让编排层注入 [system_notice]
 *   提示 LLM 继续 execute
 *
 * 不命中：返 ``{}``（不阻拦）。
 */
import type { HookHandler } from "../types.js";

/** Plan 的最小投影（与 paper service PlanRecord 字段子集对齐）。 */
export type PendingPlanLite = {
  plan_id: string;
  status: string;
  symbol: string;
  created_at?: string;
};

/** 注入式 fetcher：给定 sessionId 返回该会话内未 executed 的 plan。 */
export type PendingPlanFetcher = (sessionId: string | undefined) => Promise<PendingPlanLite[]>;

export type PendingPlanCheckOptions = {
  /** 拉 plan 的函数；不传 → handler 静默 noop（dev / 测试时友好） */
  fetcher?: PendingPlanFetcher;
  /** 命中时返回的 reason 文本模板（含 ``{count}`` / ``{ids}`` 占位） */
  reasonTemplate?: string;
};

const DEFAULT_REASON_TEMPLATE =
  "you have {count} unexecuted trade plan(s) ({ids}) in approved / pending_approval state. " +
  "complete them (trade.execute_plan with approvalToken) before ending the turn, " +
  "or explicitly trade.reject_plan if you decided not to.";

/**
 * 创建 pending-plan-check handler。
 *
 * 注意：handler 内部如果 fetcher 抛错，handler 不抛——返 ``{}`` 让 turn 正常结束。
 * 安全护栏失败不应该让用户体验崩溃。
 */
export function createPendingPlanCheckHandler(
  opts: PendingPlanCheckOptions = {},
): HookHandler {
  const fetcher = opts.fetcher;
  const template = opts.reasonTemplate ?? DEFAULT_REASON_TEMPLATE;

  return async (ctx) => {
    if (!fetcher) return {};
    let plans: PendingPlanLite[];
    try {
      plans = await fetcher(ctx.sessionId);
    } catch {
      // 拉 plan 失败：不阻 turn 结束（避免 paper service 抖动让用户卡住）
      return {};
    }
    if (plans.length === 0) return {};
    const ids = plans.map((p) => p.plan_id).join(", ");
    return {
      continue: false,
      reason: template
        .replace("{count}", String(plans.length))
        .replace("{ids}", ids),
    };
  };
}
