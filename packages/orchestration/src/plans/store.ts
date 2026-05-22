/**
 * TradePlan in-memory store —— D-8a 起步形态。
 *
 * 按 [ADR-0012 plan-exec](../../../../docs/decisions/0012-plan-exec-separation.md)：
 *
 * - LLM 不能"直接下单"，必须 createTradePlan → approveTradePlan → executeTradePlan
 * - approval_token 一次性，executed 后失效
 * - plan 有 expire_at，过期不能执行
 *
 * D-8a 简化：进程内 Map，不持久。重启即丢，仅 dev / 单 session 用。
 * D-8b 升级路径：换成 ``trade_plans`` + ``approval_tokens`` 两张 Postgres 表，
 * 接口（``create`` / ``approve`` / ``execute`` / ``get``）保持不变。
 */
import { randomUUID } from "node:crypto";

/** plan 的可执行 intent 类型（D-8a 仅 open_long / close）。 */
export type TradeIntent = "open_long" | "open_short" | "close" | "rebalance";

/** plan 状态机。 */
export type PlanStatus =
  | "pending_approval"
  | "approved"
  | "rejected"
  | "executed"
  | "expired";

/** 订单参数（与 paper /orders/submit 的字段对齐）。 */
export type OrderParams = {
  side: "BUY" | "SELL";
  type: "MARKET" | "LIMIT";
  quantity: number;
  price?: number;
  refPrice: number;
};

/** 风险参数（D-8a 占位，executeTradePlan 阶段不强制校验）。 */
export type RiskParams = {
  maxSlippageBps?: number;
  timeInForce?: "GTC" | "IOC" | "FOK";
};

/** Plan 完整状态（含 token / 时间戳）。 */
export type TradePlan = {
  planId: string;
  intent: TradeIntent;
  venue: string;
  symbol: string;
  orderParams: OrderParams;
  riskParams: RiskParams;
  rationale: string;
  status: PlanStatus;
  approvalToken: string | null;
  approvedBy: string | null;
  rejectionReason: string | null;
  createdAt: Date;
  approvedAt: Date | null;
  executedAt: Date | null;
  expireAt: Date;
  resultingOrderId: string | null;
};

/** createTradePlan 入参（公开 schema）。 */
export type CreatePlanInput = {
  intent: TradeIntent;
  venue?: string;
  symbol: string;
  orderParams: OrderParams;
  riskParams?: RiskParams;
  rationale: string;
  /** 默认 5 分钟。行情快变化，过长易让批准时和执行时脱节。 */
  expireInSeconds?: number;
};

/** approveTradePlan 入参。 */
export type ApprovePlanInput = {
  planId: string;
  approver: string;
};

/** rejectTradePlan 入参。 */
export type RejectPlanInput = {
  planId: string;
  reason: string;
  rejector: string;
};

/** ApprovePlan 的返回：token 给 LLM 在 execute 阶段携带。 */
export type ApproveResult = {
  planId: string;
  status: PlanStatus;
  approvalToken: string;
  approvedAt: Date;
};

/** 自定义错误码（给 tool description / LLM 区分处理）。 */
export class PlanError extends Error {
  public readonly code: string;
  public readonly details: Record<string, unknown>;

  constructor(code: string, message: string, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "PlanError";
    this.code = code;
    this.details = details;
  }
}

const DEFAULT_EXPIRE_SECONDS = 300;

class PlanStore {
  private readonly plans = new Map<string, TradePlan>();

  /** 写入新 plan，状态 = pending_approval。 */
  create(input: CreatePlanInput): TradePlan {
    if (!input.rationale.trim()) {
      throw new PlanError(
        "RATIONALE_REQUIRED",
        "createTradePlan: rationale must be non-empty (审计要求 LLM 解释决策动机)",
      );
    }
    if (input.orderParams.quantity <= 0) {
      throw new PlanError(
        "INVALID_QUANTITY",
        `quantity must be > 0, got ${input.orderParams.quantity}`,
      );
    }
    if (input.orderParams.type === "LIMIT" && input.orderParams.price === undefined) {
      throw new PlanError("LIMIT_REQUIRES_PRICE", "LIMIT orderParams must specify price");
    }
    if (input.orderParams.type === "MARKET" && input.orderParams.price !== undefined) {
      throw new PlanError("MARKET_NO_PRICE", "MARKET orderParams must not specify price");
    }

    const now = new Date();
    const ttl = input.expireInSeconds ?? DEFAULT_EXPIRE_SECONDS;
    const plan: TradePlan = {
      planId: randomUUID(),
      intent: input.intent,
      venue: input.venue ?? "binance",
      symbol: input.symbol,
      orderParams: { ...input.orderParams },
      riskParams: input.riskParams ?? {},
      rationale: input.rationale,
      status: "pending_approval",
      approvalToken: null,
      approvedBy: null,
      rejectionReason: null,
      createdAt: now,
      approvedAt: null,
      executedAt: null,
      expireAt: new Date(now.getTime() + ttl * 1000),
      resultingOrderId: null,
    };
    this.plans.set(plan.planId, plan);
    return plan;
  }

  /** 用 ID 查 plan。找不到返 null。 */
  get(planId: string): TradePlan | null {
    return this.plans.get(planId) ?? null;
  }

  /** 列出所有 plan（dev / 调试 / Risk Agent 扫待批准时用）。 */
  list(filter?: { status?: PlanStatus }): TradePlan[] {
    const all = Array.from(this.plans.values());
    return filter?.status ? all.filter((p) => p.status === filter.status) : all;
  }

  /** Risk Agent / 用户批准 plan。生成一次性 approval_token。 */
  approve(input: ApprovePlanInput): ApproveResult {
    const plan = this.requirePlan(input.planId);
    this.assertNotExpired(plan);

    if (plan.status !== "pending_approval") {
      throw new PlanError(
        "INVALID_STATE",
        `cannot approve plan in status '${plan.status}' (only pending_approval can be approved)`,
        { planId: plan.planId, status: plan.status },
      );
    }

    const token = randomUUID();
    const now = new Date();
    plan.status = "approved";
    plan.approvalToken = token;
    plan.approvedBy = input.approver;
    plan.approvedAt = now;

    return {
      planId: plan.planId,
      status: plan.status,
      approvalToken: token,
      approvedAt: now,
    };
  }

  /** Risk Agent / 用户拒绝 plan。终态。 */
  reject(input: RejectPlanInput): TradePlan {
    const plan = this.requirePlan(input.planId);
    if (plan.status !== "pending_approval") {
      throw new PlanError(
        "INVALID_STATE",
        `cannot reject plan in status '${plan.status}'`,
        { planId: plan.planId, status: plan.status },
      );
    }
    plan.status = "rejected";
    plan.rejectionReason = input.reason;
    plan.approvedBy = input.rejector;
    return plan;
  }

  /**
   * 消费 approval_token 把 plan 标记为 executed。
   *
   * 返回 plan（包含 orderParams，给 caller 拿去调 paper /orders/submit）。
   * **不真下单**——下单由 caller 决定（保持 store 单一职责：状态机）。
   *
   * caller 拿到结果后必须调 ``recordExecution(planId, orderId)`` 写回订单号。
   */
  consumeApproval(planId: string, approvalToken: string): TradePlan {
    const plan = this.requirePlan(planId);
    this.assertNotExpired(plan);

    if (plan.status !== "approved") {
      throw new PlanError(
        "INVALID_STATE",
        `cannot execute plan in status '${plan.status}' (must be approved)`,
        { planId, status: plan.status },
      );
    }
    if (plan.approvalToken !== approvalToken) {
      throw new PlanError(
        "INVALID_TOKEN",
        "approval_token mismatch (token 一次性，已消费或伪造)",
        { planId },
      );
    }
    // 立即作废 token（防止并发 / 重放）
    plan.approvalToken = null;
    return plan;
  }

  /** caller 完成下单后回写订单 ID + 切到 executed。 */
  recordExecution(planId: string, resultingOrderId: string): TradePlan {
    const plan = this.requirePlan(planId);
    plan.status = "executed";
    plan.executedAt = new Date();
    plan.resultingOrderId = resultingOrderId;
    return plan;
  }

  /** 全清（测试 / 重启用）。 */
  clear(): void {
    this.plans.clear();
  }

  // ─── 内部辅助 ───

  private requirePlan(planId: string): TradePlan {
    const plan = this.plans.get(planId);
    if (!plan) {
      throw new PlanError("PLAN_NOT_FOUND", `plan ${planId} not found`, { planId });
    }
    return plan;
  }

  private assertNotExpired(plan: TradePlan): void {
    if (plan.status === "executed" || plan.status === "rejected") return;
    if (new Date() > plan.expireAt) {
      plan.status = "expired";
      throw new PlanError(
        "PLAN_EXPIRED",
        `plan ${plan.planId} expired at ${plan.expireAt.toISOString()}`,
        { planId: plan.planId, expireAt: plan.expireAt.toISOString() },
      );
    }
  }
}

/** 进程单例。D-8b 换成 PG 时只需替换这个 export 的实现。 */
export const planStore = new PlanStore();

/** 测试时拿到 fresh 实例（避免 cross-test 污染）。 */
export function createPlanStore(): PlanStore {
  return new PlanStore();
}

export type { PlanStore };
