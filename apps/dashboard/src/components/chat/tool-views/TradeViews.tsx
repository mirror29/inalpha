"use client";

import { fmtNum } from "./format";
import { MetricGrid, StatusBadge, SymbolHeader } from "./primitives";

/**
 * trade.create_plan / trade.get_plan 视图:意图 + 标的 + 方向/数量/价 + 审批状态。
 *
 * 两个 tool 返回形状不同:create_plan 是 camelCase 包装(planId / orderParams /
 * createdAt),get_plan 是 { plan: PlanRecord(snake_case) }。归一化后统一渲染;
 * 形态不符回 null 由通用 ToolOutput 兜底。
 */

interface OrderParams {
  side?: string;
  type?: string;
  quantity?: number;
  price?: number;
}

interface PlanView {
  intent: string;
  symbol: string;
  venue?: string;
  status?: string;
  order: OrderParams;
  rationale?: string;
}

const INTENTS = new Set(["open_long", "open_short", "close", "rebalance"]);

/** 归一化 create_plan(camel)/ get_plan({plan})/ 裸 PlanRecord(snake)。形态不符 → null。 */
function normalizePlan(v: unknown): PlanView | null {
  if (!v || typeof v !== "object") return null;
  const root = v as Record<string, unknown>;
  // get_plan 包了一层 { plan: ... }
  const p = (
    root.plan && typeof root.plan === "object" ? root.plan : root
  ) as Record<string, unknown>;

  const intent = typeof p.intent === "string" ? p.intent : "";
  const symbol = typeof p.symbol === "string" ? p.symbol : "";
  if (!INTENTS.has(intent) || !symbol) return null;

  const op = (
    p.orderParams && typeof p.orderParams === "object"
      ? p.orderParams
      : p.order_params && typeof p.order_params === "object"
        ? p.order_params
        : {}
  ) as Record<string, unknown>;

  return {
    intent,
    symbol,
    venue: typeof p.venue === "string" ? p.venue : undefined,
    status: typeof p.status === "string" ? p.status : undefined,
    order: {
      side: typeof op.side === "string" ? op.side : undefined,
      type: typeof op.type === "string" ? op.type : undefined,
      quantity: typeof op.quantity === "number" ? op.quantity : undefined,
      price: typeof op.price === "number" ? op.price : undefined,
    },
    rationale: typeof p.rationale === "string" ? p.rationale : undefined,
  };
}

export function isPlan(v: unknown): boolean {
  return normalizePlan(v) !== null;
}

export function TradePlanView({ p: raw }: { p: unknown }) {
  const p = normalizePlan(raw);
  if (!p) return null;
  const sideTone =
    p.order.side === "BUY"
      ? "text-bull"
      : p.order.side === "SELL"
        ? "text-fox-red"
        : "text-fg";
  const metrics: { label: string; value: React.ReactNode }[] = [];
  if (p.order.side)
    metrics.push({
      label: "side",
      value: <span className={sideTone}>{p.order.side}</span>,
    });
  if (typeof p.order.quantity === "number")
    metrics.push({ label: "qty", value: fmtNum(p.order.quantity) });
  if (typeof p.order.price === "number")
    metrics.push({ label: "price", value: fmtNum(p.order.price) });
  if (p.order.type) metrics.push({ label: "type", value: p.order.type });

  return (
    <div className="flex flex-col gap-1.5">
      <SymbolHeader
        symbol={p.symbol}
        tags={[p.intent, p.venue]}
        right={p.status ? <StatusBadge status={p.status} /> : undefined}
      />
      <MetricGrid items={metrics} />
      {p.rationale && (
        <p className="text-[11px] leading-relaxed text-fg-muted">{p.rationale}</p>
      )}
    </div>
  );
}
