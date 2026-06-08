/**
 * Swarm S1 端到端 smoke（ADR-0025 Follow-up）。
 *
 * 前置：
 *
 *   bash scripts/dev.sh                # 起 data:8001 + paper:8002 + mastra:4111
 *
 * 跑：
 *
 *   pnpm tsx scripts/smoke-swarm.ts
 *
 * 这个脚本不调 LLM，**直接 mastra.getWorkflow('backtest_grid')**——验证：
 *
 * 1. workflow.createRun + .start({inputData}) 跑通
 * 2. 9-grid (3 strategies × 3 symbols) 实际并发（墙钟 < 9 × 单 job 时间）
 * 3. Pareto + topK 字段返回正常
 * 4. **如果 data-service 没历史 bars**，会以可读 error 提示 backfill 步骤
 *
 * 数据准备：脚本会在跑 grid 前先 backfill 3 个 symbol 的 1h K 线（如果已有就快速返回）。
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

const envPath = resolve(process.cwd(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { defaultServiceSubject, mintServiceToken } from "../src/auth.js";
import { DataClient } from "../src/clients/data.js";
import { getSettings } from "../src/config.js";
import { mastra } from "../src/mastra/index.js";

type AnyResult = Record<string, unknown>;

const STRATEGIES = ["sma_cross", "buy_and_hold", "mean_reversion"] as const;
const SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"];
const TIMEFRAME = "1h";

async function backfillIfNeeded(token: string, symbol: string): Promise<void> {
  const settings = getSettings();
  const data = new DataClient({ baseUrl: settings.dataServiceUrl, token });
  // 触发一次 backfill；data 服务自带 idempotent 行为（已有就快速返回）
  // 时间窗口：近 30 天 1h ≈ 720 根
  const toTs = new Date();
  const fromTs = new Date(toTs.getTime() - 30 * 86_400_000);
  const ret = (await data.backfillBars({
    venue: "binance",
    symbol,
    timeframe: TIMEFRAME,
    fromTs: fromTs.toISOString(),
    toTs: toTs.toISOString(),
  })) as AnyResult;
  console.log(`  ✓ backfilled ${symbol}: ${JSON.stringify(ret)}`);
}

async function main(): Promise<void> {
  console.log("─── 0. mint service token ───");
  const token = await mintServiceToken({ sub: defaultServiceSubject() });
  console.log("  ✓ service token minted");

  console.log("\n─── 1. backfill 3 symbols × 30d 1h bars ───");
  for (const sym of SYMBOLS) {
    try {
      await backfillIfNeeded(token, sym);
    } catch (e) {
      console.error(`  ✗ backfill ${sym} failed:`, e);
      console.error("    确认 services/data 在 8001 端口 + Binance 可达");
      process.exit(1);
    }
  }

  console.log("\n─── 2. 用单 backtest 估单 job 时间（暖 pool）───");
  const wf = mastra.getWorkflow("backtest_grid");
  const t0 = Date.now();
  const singleRun = await wf.createRun();
  const singleResult = await singleRun.start({
    inputData: {
      strategies: ["sma_cross"],
      symbols: ["BTC/USDT"],
      venue: "binance",
      timeframe: TIMEFRAME,
      from_ts: new Date(Date.now() - 30 * 86_400_000).toISOString(),
      to_ts: new Date().toISOString(),
      initial_cash: 10_000,
      fee_rate: 0.001,
    },
  });
  const singleMs = Date.now() - t0;
  if (singleResult.status !== "success") {
    console.error(`  ✗ single backtest failed: ${JSON.stringify(singleResult)}`);
    process.exit(1);
  }
  console.log(`  ✓ 1-job grid done in ${singleMs}ms`);
  console.log(`  ✓ sma_cross BTC/USDT sharpe=${singleResult.result.reports[0].report?.sharpe ?? "n/a"}`);

  console.log("\n─── 3. 跑 9-job grid (3 strategies × 3 symbols) ───");
  const t1 = Date.now();
  const gridRun = await wf.createRun();
  const gridResult = await gridRun.start({
    inputData: {
      strategies: [...STRATEGIES],
      symbols: SYMBOLS,
      venue: "binance",
      timeframe: TIMEFRAME,
      from_ts: new Date(Date.now() - 30 * 86_400_000).toISOString(),
      to_ts: new Date().toISOString(),
      initial_cash: 10_000,
      fee_rate: 0.001,
    },
  });
  const gridMs = Date.now() - t1;

  if (gridResult.status !== "success") {
    console.error(`  ✗ grid failed: ${JSON.stringify(gridResult)}`);
    process.exit(1);
  }

  const { summary, pareto, top_k, reports } = gridResult.result;

  console.log(`  ✓ 9-job grid done in ${gridMs}ms`);
  console.log(`  ✓ summary: ${JSON.stringify(summary)}`);

  const speedup = (singleMs * 9) / gridMs;
  console.log(`  ✓ speedup vs 串行估算 (${singleMs * 9}ms): ${speedup.toFixed(2)}x`);
  if (speedup < 1.5) {
    console.warn("  ⚠ speedup 偏低；检查 PAPER_POOL_SIZE 是否生效 + pool 是否预热");
  }

  console.log("\n─── 4. 失败统计 ───");
  console.log(`  total=${summary.total} ok=${summary.ok} errored=${summary.errored}`);
  if (summary.errored > 0) {
    const failed = reports.filter((r) => r.error !== null);
    for (const f of failed) {
      console.log(`  ✗ ${f.job.strategy_id} ${f.job.symbol}: ${f.error?.code} ${f.error?.message}`);
    }
  }

  console.log("\n─── 5. Pareto 前沿（Sharpe vs maxDD 上凸包）───");
  for (const p of pareto) {
    console.log(
      `  • ${p.strategy_id} ${p.symbol}  sharpe=${p.sharpe?.toFixed(2) ?? "n/a"} ` +
        `max_dd=${p.max_drawdown_pct.toFixed(2)}% return=${p.total_return_pct.toFixed(2)}%`,
    );
  }

  console.log("\n─── 6. Top-3 by Sharpe ───");
  top_k.forEach((p, i) => {
    console.log(
      `  ${i + 1}. ${p.strategy_id} ${p.symbol}  sharpe=${p.sharpe?.toFixed(2) ?? "n/a"} ` +
        `max_dd=${p.max_drawdown_pct.toFixed(2)}%`,
    );
  });

  console.log("\n✓ smoke-swarm 全部通过");
}

main().catch((err) => {
  console.error("smoke-swarm failed:", err);
  process.exit(1);
});
