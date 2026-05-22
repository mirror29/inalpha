/**
 * D-8a plan/exec 真服务 e2e smoke test。
 *
 * 前置：services/data + services/paper 都在跑。
 *
 *   pnpm tsx scripts/smoke-plan.ts
 *
 * 这个脚本不调 LLM，**直接调 tool 函数模拟 agent 行为**：
 *
 * 1. trader 角度：data.get_bars 拿 refPrice → trade.create_plan
 * 2. risk 角度：trade.get_plan 看内容 → trade.approve_plan 发 token
 * 3. trader 角度：trade.execute_plan(planId, token) → paper /orders/submit → 拿 order result
 * 4. 跑一遍"未审批就执行"的拒绝路径，确认护栏生效
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

const envPath = resolve(process.cwd(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { mintServiceToken } from "../src/auth.js";
import {
  approveTradePlanTool,
  createTradePlanTool,
  dataGetBarsTool,
  executeTradePlanTool,
  getTradePlanTool,
} from "../src/tools/index.js";

type AnyResult = Record<string, unknown>;

async function main(): Promise<void> {
  const token = await mintServiceToken({ sub: "service:smoke" });
  const ctx = { requestContext: { authToken: token } } as never;

  console.log("─── 1. [trader] 取最近 1 根 BTC/USDT 1h bar 当 refPrice ───");
  const bars = (await dataGetBarsTool.execute!(
    {
      venue: "binance",
      symbol: "BTC/USDT",
      timeframe: "1h",
      limit: 1,
    } as never,
    ctx,
  )) as { bars: { close: number; ts: string }[]; count: number };
  if (bars.count === 0) {
    throw new Error("no bars in DB; run `pnpm smoke` first to backfill");
  }
  const latest = bars.bars[bars.bars.length - 1];
  const refPrice = latest.close;
  console.log(`refPrice = ${refPrice} @ ${latest.ts}`);

  console.log("\n─── 2. [trader] trade.create_plan(open_long, 0.0001 BTC, MARKET) ───");
  const planResult = (await createTradePlanTool.execute!(
    {
      intent: "open_long",
      venue: "binance",
      symbol: "BTC/USDT",
      side: "BUY",
      orderType: "MARKET",
      quantity: 0.0001,
      refPrice,
      rationale: "smoke test：MARKET 单跑 plan/exec 链路",
      expireInSeconds: 300,
    } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(planResult, null, 2));
  if (!planResult.ok) {
    throw new Error(`create_plan failed: ${JSON.stringify(planResult)}`);
  }
  const planId = planResult.planId as string;

  console.log("\n─── 3. [trader, 反例] 不审批直接 execute → 应被拒 ───");
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

  console.log("\n─── 4. [risk] trade.get_plan 查看 plan 内容 ───");
  const got = (await getTradePlanTool.execute!({ planId } as never, ctx)) as AnyResult;
  console.log(JSON.stringify(got, null, 2));

  console.log("\n─── 5. [risk] trade.approve_plan → 拿 token ───");
  const approval = (await approveTradePlanTool.execute!(
    { planId, approver: "risk-agent" } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(approval, null, 2));
  if (!approval.ok) {
    throw new Error(`approve failed: ${JSON.stringify(approval)}`);
  }
  const approvalToken = approval.approvalToken as string;

  console.log("\n─── 6. [trader] trade.execute_plan(planId, token) ───");
  const exec = (await executeTradePlanTool.execute!(
    { planId, approvalToken } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(exec, null, 2));
  if (!exec.ok) {
    throw new Error(`execute failed: ${JSON.stringify(exec)}`);
  }

  console.log("\n─── 7. [trader, 反例] 二次 execute 同 token → 应被拒（token 一次性） ───");
  const replay = (await executeTradePlanTool.execute!(
    { planId, approvalToken } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(replay, null, 2));
  if (replay.ok) {
    throw new Error("expected replay execute to fail");
  }
  console.log("✓ token 一次性护栏生效");

  console.log("\n─── 8. [trader] trade.get_plan 看终态 ───");
  const finalState = (await getTradePlanTool.execute!(
    { planId } as never,
    ctx,
  )) as AnyResult;
  console.log(JSON.stringify(finalState, null, 2));

  console.log("\n─── ✅ D-8a plan/exec smoke PASSED ───");
}

main().catch((err) => {
  console.error("✗ smoke-plan FAILED:");
  console.error(err);
  process.exit(1);
});
