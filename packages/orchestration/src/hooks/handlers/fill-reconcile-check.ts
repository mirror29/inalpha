/**
 * ``fill-reconcile-check`` —— Stop hook handler。
 *
 * 用途（ADR-0010 §Stop hook 补丁场景 3）：live session 里检查最近时间窗口内是否有
 * "已下单但 broker 还没回执"的 unreconciled fill。如果有，强拽 LLM 再 turn 一次
 * 让它说明 / 重查 / 标记 stuck。
 *
 * D-8c 现状：paper service 撮合是**同步**的（OrderExecutor.execute 即时返成交），
 * 没有 broker 回执延迟概念。但本 handler 已经为 D-10+ live 接入预留：
 *
 * - paper service 的 orders 表里 status='FILLED' 但 ``avg_fill_price=null`` 即视为
 *   未 reconcile（已发出但 broker 未回价）
 * - 注入式 fetcher 让接入路径与 store 解耦；测试给固定列表即可
 *
 * 命中：返 ``{ continue: false, reason }``。不命中：返 ``{}``。
 */
import type { HookHandler } from "../types.js";

/** 未 reconcile 订单的最小投影。 */
export type UnreconciledOrderLite = {
  client_order_id: string;
  symbol: string;
  venue: string;
  age_seconds: number;
};

/** 注入式 fetcher：给定 sessionId 返回最近窗口内 unreconciled 订单。 */
export type UnreconciledFetcher = (
  sessionId: string | undefined,
) => Promise<UnreconciledOrderLite[]>;

export type FillReconcileCheckOptions = {
  /** 拉未 reconcile 订单的函数；不传 → handler 静默 noop */
  fetcher?: UnreconciledFetcher;
  /** 命中时返回的 reason 文本模板（``{count}`` / ``{ids}``） */
  reasonTemplate?: string;
  /** 只检查 age ≥ 此秒数的（默认 30s：低于此值视为"刚发，broker 没来得及回"） */
  minAgeSeconds?: number;
};

const DEFAULT_REASON_TEMPLATE =
  "found {count} unreconciled order(s) ({ids}) older than the reconcile threshold. " +
  "before ending the turn, query paper.list_orders for each to confirm fill state, " +
  "or explicitly tell the user the orders are stuck.";

export function createFillReconcileCheckHandler(
  opts: FillReconcileCheckOptions = {},
): HookHandler {
  const fetcher = opts.fetcher;
  const template = opts.reasonTemplate ?? DEFAULT_REASON_TEMPLATE;
  const minAge = opts.minAgeSeconds ?? 30;

  return async (ctx) => {
    if (!fetcher) return {};
    let orders: UnreconciledOrderLite[];
    try {
      orders = await fetcher(ctx.sessionId);
    } catch {
      return {};
    }
    const stale = orders.filter((o) => o.age_seconds >= minAge);
    if (stale.length === 0) return {};
    const ids = stale.map((o) => o.client_order_id).join(", ");
    return {
      continue: false,
      reason: template
        .replace("{count}", String(stale.length))
        .replace("{ids}", ids),
    };
  };
}
