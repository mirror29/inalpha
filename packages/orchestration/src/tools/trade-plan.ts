/**
 * Plan / Exec 两阶段 trade tool —— D-8a 起步形态。
 *
 * 按 [ADR-0012](../../../../docs/decisions/0012-plan-exec-separation.md)：
 *
 * 1. ``createTradePlan`` —— LLM 把"想下单"翻成持久化 plan，状态 pending_approval
 * 2. ``approveTradePlan`` —— Risk Agent / 用户审批，发放一次性 approval_token
 * 3. ``executeTradePlan`` —— 凭 token 真正调 paper /orders/submit 下单
 *
 * **关键约束**：LLM 没有"直接下单"路径（``paper.submit_order`` tool 不存在）。
 * 唯一可达路径就是 createPlan → approve → execute。
 *
 * D-8a 是 in-memory store + 同步链路（approve 立刻发 token、execute 立刻拿到成交）。
 * D-8b 升级到 Postgres + askUserChoice 中断让用户审。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken } from "../auth.js";
import { PaperClient } from "../clients/paper.js";
import { getSettings } from "../config.js";
import { PlanError, planStore } from "../plans/store.js";

const SymbolSchema = z
  .string()
  .regex(/^[A-Z0-9]+\/[A-Z0-9]+$/, "symbol 必须是 CCXT 风格 'BASE/QUOTE'");

const SideSchema = z.enum(["BUY", "SELL"]);
const TypeSchema = z.enum(["MARKET", "LIMIT"]);
const IntentSchema = z.enum(["open_long", "open_short", "close", "rebalance"]);

type ToolRequestContext = { authToken?: string };

async function getPaperClient(ctx?: ToolRequestContext): Promise<PaperClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: "service:orchestration" }));
  return new PaperClient({ baseUrl: settings.paperServiceUrl, token });
}

/**
 * PlanError 兼容输出：所有 plan tool 都把 PlanError 翻成 ``{ ok: false, code, message }``
 * 返回，LLM 拿到 ``ok: false`` 就知道该改路径而不是抛异常。
 *
 * 非 PlanError（如网络故障）继续 throw —— 让 Mastra / HttpClientError 中间件处理。
 */
function planErrorToResult(err: unknown): { ok: false; code: string; message: string; details: Record<string, unknown> } | null {
  if (err instanceof PlanError) {
    return { ok: false, code: err.code, message: err.message, details: err.details };
  }
  return null;
}

// ────────────────────────────────────────────────────────────────────
// createTradePlan
// ────────────────────────────────────────────────────────────────────

export const createTradePlanTool = createTool({
  id: "trade.create_plan",
  description: `
    把"想下单"的意图落成一个持久化 plan，状态 = pending_approval。**本工具不下单**。

    何时用：
    - 用户 / 策略要执行一笔交易（开仓 / 平仓 / 调仓）
    - 任何 paper / live 下单都必须走这个，没有"直接下单"的快捷路径

    何时不用：
    - 只是回测 / 查行情（走 paper.run_backtest / data.get_bars）
    - 还在分析策略可行性（先回测，不要创建实盘 plan）

    坑：
    - **rationale 必填**：必须解释"为什么要下这单"，给 Risk Agent 判断 + 复盘审计
    - 返回 'planId' 后必须先 trade.approve_plan，再 trade.execute_plan
    - **不要**自己 approve 自己（trader agent 应该只 create + execute，approve 由 risk agent 做）
    - 默认 5 分钟过期；行情快变化的 plan 别批太久才执行
    - LIMIT 必须给 price，MARKET 必须不给 price
    - refPrice 是撮合参考价（D-8a 必填）；调用前可先用 data.get_bars 拿最近的 close 当 refPrice
  `.trim(),
  inputSchema: z.object({
    intent: IntentSchema.describe("交易意图：open_long / open_short / close / rebalance"),
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    side: SideSchema.describe("BUY / SELL"),
    orderType: TypeSchema.default("MARKET").describe("MARKET / LIMIT"),
    quantity: z.number().positive().describe("下单数量（base 资产单位，例如 BTC 数量）"),
    price: z.number().positive().optional().describe("LIMIT 必填；MARKET 必须省略"),
    refPrice: z.number().positive().describe("撮合参考价（用 data.get_bars 拿最近 close 价）"),
    rationale: z
      .string()
      .min(1)
      .describe("解释为什么要下这单——给 Risk Agent / 用户审计用，必填"),
    expireInSeconds: z
      .number()
      .int()
      .positive()
      .max(3600)
      .default(300)
      .describe("plan 过期时间秒数；默认 300（5 分钟）"),
  }),
  execute: async (inputData) => {
    try {
      const plan = planStore.create({
        intent: inputData.intent,
        venue: inputData.venue ?? "binance",
        symbol: inputData.symbol,
        orderParams: {
          side: inputData.side,
          type: inputData.orderType ?? "MARKET",
          quantity: inputData.quantity,
          price: inputData.price,
          refPrice: inputData.refPrice,
        },
        rationale: inputData.rationale,
        expireInSeconds: inputData.expireInSeconds ?? 300,
      });
      return {
        ok: true as const,
        planId: plan.planId,
        status: plan.status,
        intent: plan.intent,
        symbol: plan.symbol,
        venue: plan.venue,
        orderParams: plan.orderParams,
        rationale: plan.rationale,
        createdAt: plan.createdAt.toISOString(),
        expireAt: plan.expireAt.toISOString(),
        approvalRequiredBy: "risk-agent-or-user",
      };
    } catch (err) {
      const r = planErrorToResult(err);
      if (r) return r;
      throw err;
    }
  },
});

// ────────────────────────────────────────────────────────────────────
// approveTradePlan
// ────────────────────────────────────────────────────────────────────

export const approveTradePlanTool = createTool({
  id: "trade.approve_plan",
  description: `
    审批一个 pending plan，发放一次性 approval_token（给 trade.execute_plan 用）。

    何时用：
    - 你是 Risk Agent，刚审完一个 plan 觉得安全可放行
    - 用户在 UI 上点了"批准"

    何时不用：
    - **不要自己 create + approve 自己的 plan**（角色对抗失效）
    - plan 已是 approved / executed / rejected / expired → 拒绝

    坑：
    - approval_token 一次性，executed 后立刻作废（防重放）
    - 风险审查必须在调用本 tool 之前完成（本 tool 不做风控）
    - approver 字符串建议带"risk-agent" / "user:<id>" 前缀，便于审计
  `.trim(),
  inputSchema: z.object({
    planId: z.string().uuid().describe("trade.create_plan 返回的 planId"),
    approver: z
      .string()
      .min(1)
      .describe("批准者标识，建议 'risk-agent' 或 'user:<id>'，落审计 log"),
  }),
  execute: async (inputData) => {
    try {
      const result = planStore.approve({
        planId: inputData.planId,
        approver: inputData.approver,
      });
      return {
        ok: true as const,
        planId: result.planId,
        status: result.status,
        approvalToken: result.approvalToken,
        approvedAt: result.approvedAt.toISOString(),
      };
    } catch (err) {
      const r = planErrorToResult(err);
      if (r) return r;
      throw err;
    }
  },
});

// ────────────────────────────────────────────────────────────────────
// rejectTradePlan
// ────────────────────────────────────────────────────────────────────

export const rejectTradePlanTool = createTool({
  id: "trade.reject_plan",
  description: `
    拒绝一个 pending plan（终态）。Risk Agent 觉得不安全用这个。

    何时用：
    - Risk Agent 审计后决定拒绝（违反风控 / 与持仓冲突 / 行情异常）
    - 用户在 UI 上点"拒绝"

    何时不用：
    - 想要"稍后再批"：让 plan 自然过期即可
    - plan 已是终态：noop

    坑：
    - reason 必填，给复盘 / 投诉用
    - 一旦 reject，同样意图想下单必须新建一个 plan（不能复用 planId）
  `.trim(),
  inputSchema: z.object({
    planId: z.string().uuid(),
    reason: z.string().min(1).describe("拒绝原因，给复盘审计用"),
    rejector: z.string().min(1).describe("'risk-agent' 或 'user:<id>'"),
  }),
  execute: async (inputData) => {
    try {
      const plan = planStore.reject({
        planId: inputData.planId,
        reason: inputData.reason,
        rejector: inputData.rejector,
      });
      return {
        ok: true as const,
        planId: plan.planId,
        status: plan.status,
        rejectionReason: plan.rejectionReason,
      };
    } catch (err) {
      const r = planErrorToResult(err);
      if (r) return r;
      throw err;
    }
  },
});

// ────────────────────────────────────────────────────────────────────
// executeTradePlan
// ────────────────────────────────────────────────────────────────────

export const executeTradePlanTool = createTool({
  id: "trade.execute_plan",
  description: `
    凭 approval_token 真正下单：调 paper /orders/submit 并把 plan 标记为 executed。

    何时用：
    - trader agent 拿到 risk agent 发的 approval_token 后下单
    - plan 仍在过期前

    何时不用：
    - 没有 approval_token → 先 trade.approve_plan
    - plan 已 executed / rejected / expired → 拒绝
    - 想"只看一下不真下" → 不需要这步（plan 本身已落库审计）

    坑：
    - approval_token 一次性，执行成功后立刻作废，**不能重试**
    - 若 paper 返 REJECTED（LIMIT 未触发等），plan 仍标记为 executed（订单已尝试），
      result.status="REJECTED"——LLM 必须新建 plan 重试，不能复用旧 plan
    - 网络故障导致 paper /orders/submit 抛 HttpClientError 时，**plan 状态保持 approved**，
      token 没消费（caller 可重试同样的 execute 调用）
  `.trim(),
  inputSchema: z.object({
    planId: z.string().uuid(),
    approvalToken: z.string().uuid().describe("trade.approve_plan 返回的 token"),
  }),
  execute: async (inputData, ctx) => {
    // 1. 校验 token + plan 状态 + 消费 token（in-memory 原子）
    let plan;
    try {
      plan = planStore.consumeApproval(inputData.planId, inputData.approvalToken);
    } catch (err) {
      const r = planErrorToResult(err);
      if (r) return r;
      throw err;
    }

    // 2. 调 paper /orders/submit
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getPaperClient(tc);
    const orderResult = await client.submitOrder({
      venue: plan.venue,
      symbol: plan.symbol,
      side: plan.orderParams.side,
      type: plan.orderParams.type,
      quantity: plan.orderParams.quantity,
      price: plan.orderParams.price,
      refPrice: plan.orderParams.refPrice,
    });

    // 3. 回写订单 ID + 状态切到 executed（即使 order 被 venue REJECTED 也是 executed
    //    —— 因为"已尝试下单"是事实；REJECTED 由 result.status 表达）
    planStore.recordExecution(plan.planId, orderResult.client_order_id);

    return {
      ok: true as const,
      planId: plan.planId,
      planStatus: "executed" as const,
      order: {
        clientOrderId: orderResult.client_order_id,
        status: orderResult.status,
        filledQuantity: orderResult.filled_quantity,
        avgFillPrice: orderResult.avg_fill_price,
        fee: orderResult.fee,
        notional: orderResult.notional,
        rejectionReason: orderResult.rejection_reason,
      },
    };
  },
});

// ────────────────────────────────────────────────────────────────────
// getTradePlan（dev / 调试）
// ────────────────────────────────────────────────────────────────────

export const getTradePlanTool = createTool({
  id: "trade.get_plan",
  description: `
    按 planId 查 plan 状态。Risk Agent 审批前用一下、或用户问"我那个 plan 怎么样了"用。

    何时用：
    - Risk Agent 拿到 planId 后先 get 看完整内容再决策
    - 用户问"我那个 plan 还在不在"

    何时不用：
    - 已经手里有完整 plan dict（create 刚返回） → 不需要再 get
  `.trim(),
  inputSchema: z.object({
    planId: z.string().uuid(),
  }),
  execute: async (inputData) => {
    const plan = planStore.get(inputData.planId);
    if (!plan) {
      return { ok: false as const, code: "PLAN_NOT_FOUND", message: `plan ${inputData.planId} not found` };
    }
    return {
      ok: true as const,
      plan: {
        planId: plan.planId,
        intent: plan.intent,
        venue: plan.venue,
        symbol: plan.symbol,
        orderParams: plan.orderParams,
        rationale: plan.rationale,
        status: plan.status,
        approvedBy: plan.approvedBy,
        rejectionReason: plan.rejectionReason,
        createdAt: plan.createdAt.toISOString(),
        approvedAt: plan.approvedAt?.toISOString() ?? null,
        executedAt: plan.executedAt?.toISOString() ?? null,
        expireAt: plan.expireAt.toISOString(),
        resultingOrderId: plan.resultingOrderId,
      },
    };
  },
});

export const tradePlanTools = [
  createTradePlanTool,
  approveTradePlanTool,
  rejectTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
] as const;
