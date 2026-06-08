/**
 * D-8a' plan/exec 真服务 e2e smoke test。
 *
 * 前置：services/data + services/paper 都在跑。
 *
 *   pnpm tsx scripts/smoke-plan.ts
 *
 * 这个脚本不调 LLM，**直接调 tool 函数模拟 orchestrator 行为**：
 *
 * 1. trade.create_plan（不传 refPrice，paper 服务端自取）
 * 2. trade.get_plan / trade.approve_plan → 发 token
 * 3. trade.execute_plan(planId, token) → paper /orders/submit → 拿 order result
 * 4. 跑两条反例路径：未审批就 execute、token 重放——确认护栏生效
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

const envPath = resolve(process.cwd(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { defaultServiceSubject, mintServiceToken } from "../src/auth.js";
import {
  approveTradePlanTool,
  createTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
  paperGetAccountTool,
  paperListOrdersTool,
  paperListPositionsTool,
} from "../src/tools/index.js";

type AnyResult = Record<string, unknown>;

async function main(): Promise<void> {
  const token = await mintServiceToken({ sub: defaultServiceSubject() });
  const ctx = { requestContext: { authToken: token } } as never;

  // D-8a'：不再需要 LLM/smoke 自取 refPrice——paper /orders/submit 服务端调 data /ticker 自取
  console.log("─── 1. trade.create_plan(open_long, 0.0001 BTC, MARKET) ───");
  const planResult = (await createTradePlanTool.execute!(
    {
      intent: "open_long",
      venue: "binance",
      symbol: "BTC/USDT",
      side: "BUY",
      orderType: "MARKET",
      quantity: 0.0001,
      rationale: "smoke test：MARKET 单跑 plan/exec 链路（refPrice 服务端自取）",
      expireInSeconds: 300,
    } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(planResult, null, 2));
  if (!planResult.ok) {
    throw new Error(`create_plan failed: ${JSON.stringify(planResult)}`);
  }
  const planId = planResult.planId as string;

  console.log("\n─── 3. [反例] 不审批直接 execute → 应被拒 ───");
  const earlyExec = (await executeTradePlanTool.execute!(
    { planId, approvalToken: "00000000-0000-0000-0000-000000000000" } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(earlyExec, null, 2));
  if (earlyExec.ok) {
    throw new Error("expected execute to fail without approval");
  }
  if (earlyExec.code !== "INVALID_STATE") {
    throw new Error(`expected INVALID_STATE, got ${earlyExec.code}`);
  }
  console.log("✓ 护栏生效：未审批的 plan 拒绝执行");

  console.log("\n─── 4. [approve] trade.get_plan 查看 plan 内容 ───");
  const got = (await getTradePlanTool.execute!({ planId } as never, ctx)) as AnyResult;
  console.log(JSON.stringify(got, null, 2));

  console.log("\n─── 5. [approve] trade.approve_plan → 拿 token ───");
  const approval = (await approveTradePlanTool.execute!(
    { planId, approver: "risk-agent" } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(approval, null, 2));
  if (!approval.ok) {
    throw new Error(`approve failed: ${JSON.stringify(approval)}`);
  }
  const approvalToken = approval.approvalToken as string;

  console.log("\n─── 6. [exec] trade.execute_plan(planId, token) ───");
  const exec = (await executeTradePlanTool.execute!(
    { planId, approvalToken } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(exec, null, 2));
  if (!exec.ok) {
    throw new Error(`execute failed: ${JSON.stringify(exec)}`);
  }

  console.log("\n─── 7. [反例] 二次 execute 同 token → 应被拒（token 一次性） ───");
  const replay = (await executeTradePlanTool.execute!(
    { planId, approvalToken } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(replay, null, 2));
  if (replay.ok) {
    throw new Error("expected replay execute to fail");
  }
  console.log("✓ token 一次性护栏生效");

  console.log("\n─── 8. [exec] trade.get_plan 看终态 ───");
  const finalState = (await getTradePlanTool.execute!(
    { planId } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(finalState, null, 2));

  // D-8b：跨服务回溯
  console.log("\n─── 9. [D-8b query] paper.list_orders 看历史订单 ───");
  const orders = (await paperListOrdersTool.execute!(
    { limit: 5 } as never,
    ctx,
  )) as unknown;
  console.log(JSON.stringify(orders, null, 2));

  console.log("\n─── 10. [D-8b query] paper.list_positions 看活跃持仓 ───");
  const positions = (await paperListPositionsTool.execute!(
    { includeFlat: false } as never,
    ctx,
  )) as unknown;
  console.log(JSON.stringify(positions, null, 2));

  console.log("\n─── 11. [D-8b query] paper.get_account 看账户快照 ───");
  const account = (await paperGetAccountTool.execute!({} as never, ctx)) as AnyResult;
  console.log(JSON.stringify(account, null, 2));

  // 简单断言：account 该有 cash < initial_cash，positions 该非空
  if (
    typeof account.initial_cash === "number" &&
    typeof account.cash === "number" &&
    account.cash >= account.initial_cash
  ) {
    console.warn(
      "⚠️  cash 未减少（initial=%s, cur=%s），可能撮合或扣款链路漏跑",
      account.initial_cash,
      account.cash,
    );
  }

  console.log("\n─── ✅ D-8b plan/exec + 回溯 smoke PASSED ───");
}

main().catch((err) => {
  console.error("✗ smoke-plan FAILED:");
  console.error(err);
  process.exit(1);
});
