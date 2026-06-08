/**
 * Plan / Exec 两阶段 trade tool —— D-8b 形态（全部走 paper service HTTP API）。
 *
 * 按 [ADR-0012](../../../../docs/decisions/0012-plan-exec-separation.md)：
 *
 * 1. ``trade.create_plan`` —— POST /plans
 * 2. ``trade.approve_plan`` —— POST /plans/{id}/approve（拿一次性 approval_token）
 * 3. ``trade.reject_plan``  —— POST /plans/{id}/reject
 * 4. ``trade.execute_plan`` —— POST /plans/{id}/execute（凭 token 在 paper 内一把
 *                              撮合 + 落 orders / positions / cash）
 * 5. ``trade.get_plan``     —— GET /plans/{id}
 *
 * **关键约束**（不变）：LLM 没有"直接下单"路径，唯一可达就是 create → approve → execute。
 *
 * D-8b 改动：
 * - 之前的本地 PlanStore（进程内 Map）已删除，全部状态在 paper service 的 trade_plans 表
 * - 接口契约不变：tool 名 / inputSchema / 返回 ``{ ok: true|false, ... }`` 形态保持
 * - 按 account 隔离由 JWT forward + paper 服务端 ``account_id_from_user`` 实现
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { defaultServiceSubject, mintServiceToken } from "../auth.js";
import { HttpClientError } from "../clients/http.js";
import { PaperClient } from "../clients/paper.js";
import { getSettings } from "../config.js";

// D-9 multi-market：与 tools/data.ts 保持一致。
const SymbolSchema = z
  .string()
  .min(1)
  .max(50)
  .regex(
    /^[\^A-Za-z0-9._/-]+$/,
    "symbol 不能为空 / 含空格；支持 crypto 'BTC/USDT' / 普通 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
  );

const SideSchema = z.enum(["BUY", "SELL"]);
const TypeSchema = z.enum(["MARKET", "LIMIT"]);
const IntentSchema = z.enum(["open_long", "open_short", "close", "rebalance"]);

type ToolRequestContext = { authToken?: string };

async function getClient(ctx?: ToolRequestContext): Promise<PaperClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
  return new PaperClient({ baseUrl: settings.paperServiceUrl, token });
}

/**
 * 把 paper 返回的业务错误（HttpClientError code in {PLAN_NOT_FOUND, INVALID_STATE,
 * PLAN_EXPIRED, RATIONALE_REQUIRED, ...}）翻成 ``{ ok: false, code, message }``。
 *
 * 非业务错误（5xx / 网络）继续 throw，让 Mastra runtime / hook layer 处理。
 */
function httpErrorToResult(
  err: unknown,
): { ok: false; code: string; message: string; details: Record<string, unknown> } | null {
  if (!(err instanceof HttpClientError)) return null;
  // 4xx 视为业务错误，5xx 视为系统错误重新抛
  if (err.status < 400 || err.status >= 500) return null;
  return {
    ok: false,
    code: err.code,
    message: err.message,
    details: err.details,
  };
}

// ────────────────────────────────────────────────────────────────────
// createTradePlan
// ────────────────────────────────────────────────────────────────────

export const createTradePlanTool = createTool({
  id: "trade.create_plan",
  description: `
    把"想下单"的意图落成持久化 plan（PG 表，按 account 隔离）。**本工具不下单**。

    何时用：
    - 用户要执行一笔交易（开仓 / 平仓 / 调仓）

    何时不用：
    - 只是回测 / 查行情（走 paper.run_backtest / data.get_bars）

    坑：
    - rationale 必填
    - 返回 'planId' 后顺序调 trade.approve_plan → trade.execute_plan 即可
    - 默认 5 分钟过期
    - LIMIT 必须给 price，MARKET 必须不给 price
    - **refPrice 不再由 LLM 提供**：paper /orders/submit 服务端自取

    D-8c 起：可传 researchId / backtestRunId 把"分析→回测→下单"血缘链上。这两个字段
    会被 prefix 进 rationale（["research:<uuid>", "backtest:<uuid>"] + 用户 rationale），
    后续审计 / 复盘可直接 grep。
  `.trim(),
  inputSchema: z.object({
    intent: IntentSchema.describe("交易意图：open_long / open_short / close / rebalance"),
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    side: SideSchema,
    orderType: TypeSchema.default("MARKET"),
    quantity: z.number().positive(),
    price: z.number().positive().optional().describe("LIMIT 必填；MARKET 必须省略"),
    rationale: z.string().min(1).describe("解释下单动机——审计 / 风控用，必填"),
    researchId: z
      .string()
      .uuid()
      .optional()
      .describe("D-8c 血缘：上游 research.deep_dive 的 research_id"),
    backtestRunId: z
      .string()
      .uuid()
      .optional()
      .describe("D-8c 血缘：触发本次下单的 paper.run_backtest 的 run_id"),
    expireInSeconds: z.number().int().positive().max(3600).default(300),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    // 拼血缘前缀。例如 rationale = "[research:<id>] [backtest:<id>] 用户原因"。
    const prefixParts: string[] = [];
    if (inputData.researchId) prefixParts.push(`[research:${inputData.researchId}]`);
    if (inputData.backtestRunId) prefixParts.push(`[backtest:${inputData.backtestRunId}]`);
    const rationaleWithLineage = prefixParts.length
      ? `${prefixParts.join(" ")} ${inputData.rationale}`
      : inputData.rationale;
    try {
      const plan = await client.createPlan({
        intent: inputData.intent,
        venue: inputData.venue ?? "binance",
        symbol: inputData.symbol,
        side: inputData.side,
        orderType: inputData.orderType ?? "MARKET",
        quantity: inputData.quantity,
        price: inputData.price,
        rationale: rationaleWithLineage,
        expireInSeconds: inputData.expireInSeconds ?? 300,
      });
      return {
        ok: true as const,
        planId: plan.plan_id,
        status: plan.status,
        intent: plan.intent,
        symbol: plan.symbol,
        venue: plan.venue,
        orderParams: plan.order_params,
        rationale: plan.rationale,
        createdAt: plan.created_at,
        expireAt: plan.expire_at,
        approvalRequiredBy: "risk-agent-or-user",
      };
    } catch (err) {
      const r = httpErrorToResult(err);
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
    审批一个 pending plan，paper 服务端发放一次性 approval_token。

    何时用：
    - 自动化路径：orchestrator 调完 create_plan 立刻调本工具批准（小额）
    - 用户在 UI 上点"批准"

    坑：
    - approval_token 一次性，executed 后立刻作废
    - 风险审查应在调用本工具之前完成（本工具不做风控，仅发 token）
    - approver 字符串建议带 'risk-agent' / 'user:<id>' 前缀，便于审计
  `.trim(),
  inputSchema: z.object({
    planId: z.string().uuid(),
    approver: z.string().min(1),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    try {
      const plan = await client.approvePlan(inputData.planId, inputData.approver);
      return {
        ok: true as const,
        planId: plan.plan_id,
        status: plan.status,
        approvalToken: plan.approval_token,
        approvedAt: plan.approved_at,
      };
    } catch (err) {
      const r = httpErrorToResult(err);
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
    拒绝一个 pending plan（终态）。

    坑：reason 必填；终态不可改。
  `.trim(),
  inputSchema: z.object({
    planId: z.string().uuid(),
    reason: z.string().min(1),
    rejector: z.string().min(1),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    try {
      const plan = await client.rejectPlan(
        inputData.planId,
        inputData.reason,
        inputData.rejector,
      );
      return {
        ok: true as const,
        planId: plan.plan_id,
        status: plan.status,
        rejectionReason: plan.rejection_reason,
      };
    } catch (err) {
      const r = httpErrorToResult(err);
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
    凭 approval_token 真下单：paper 服务端原子完成 consume_token + 撮合 + 落 orders + 更新 positions/cash + 切 plan=executed。

    何时用：
    - 拿到 trade.approve_plan 返回的 approvalToken 后

    坑：
    - approval_token 一次性
    - 若 paper 返回 status=REJECTED（LIMIT 未触发等），plan 仍标 executed
    - 网络故障时 paper 事务会回滚，token 不消费，可重试
  `.trim(),
  inputSchema: z.object({
    planId: z.string().uuid(),
    approvalToken: z.string().min(1),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    try {
      const r = await client.executePlan(inputData.planId, inputData.approvalToken);
      return {
        ok: true as const,
        planId: r.plan_id,
        planStatus: r.plan_status,
        order: {
          clientOrderId: r.order.client_order_id,
          status: r.order.status,
          filledQuantity: r.order.filled_quantity,
          avgFillPrice: r.order.avg_fill_price,
          fee: r.order.fee,
          notional: r.order.notional,
          rejectionReason: r.order.rejection_reason,
        },
      };
    } catch (err) {
      const errResult = httpErrorToResult(err);
      if (errResult) return errResult;
      throw err;
    }
  },
});

// ────────────────────────────────────────────────────────────────────
// getTradePlan
// ────────────────────────────────────────────────────────────────────

export const getTradePlanTool = createTool({
  id: "trade.get_plan",
  description: `
    按 planId 查 plan 状态。用户问"我那个 plan 怎么样了"时调。
  `.trim(),
  inputSchema: z.object({
    planId: z.string().uuid(),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    try {
      const plan = await client.getPlan(inputData.planId);
      return {
        ok: true as const,
        plan: {
          planId: plan.plan_id,
          intent: plan.intent,
          venue: plan.venue,
          symbol: plan.symbol,
          orderParams: plan.order_params,
          rationale: plan.rationale,
          status: plan.status,
          approvedBy: plan.approved_by,
          rejectionReason: plan.rejection_reason,
          createdAt: plan.created_at,
          approvedAt: plan.approved_at,
          executedAt: plan.executed_at,
          expireAt: plan.expire_at,
          resultingOrderId: plan.resulting_order_id,
        },
      };
    } catch (err) {
      const r = httpErrorToResult(err);
      if (r) return r;
      throw err;
    }
  },
});

export const tradePlanTools = [
  createTradePlanTool,
  approveTradePlanTool,
  rejectTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
] as const;
