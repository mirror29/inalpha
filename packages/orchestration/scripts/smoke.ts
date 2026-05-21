/**
 * 真服务 e2e smoke test —— 跟 services/data + services/paper 联调。
 *
 * 用法（先起两个 service）：
 *
 *   pnpm smoke
 *
 * 这个脚本会：
 * 1. 自签一个 service JWT
 * 2. backfill 7 天 BTC/USDT 1h 数据
 * 3. list_strategies
 * 4. 跑 SMA cross 回测
 * 5. 打印报告
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

// 显式加载 .env（Node 24+ 自带 loadEnvFile；--env-file 在 pnpm script wrapper
// 下传递偶有问题，这里保险）
const envPath = resolve(process.cwd(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { mintServiceToken } from "../src/auth.js";
import {
  dataBackfillBarsTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
} from "../src/tools/index.js";

async function main(): Promise<void> {
  // 1. 自签 token（JWT_SECRET 从 .env / 环境读）
  const token = await mintServiceToken({ sub: "service:smoke" });
  const rc = { authToken: token };

  // 用过去 7 天作为 demo（系统时区无所谓）
  const toTs = new Date();
  const fromTs = new Date(toTs.getTime() - 7 * 24 * 3600 * 1000);

  console.log("─── 1. backfill 7d BTC/USDT 1h ───");
  const backfill = await dataBackfillBarsTool.execute!({
    context: {
      venue: "binance",
      symbol: "BTC/USDT",
      timeframe: "1h",
      fromTs: fromTs.toISOString(),
      toTs: toTs.toISOString(),
    },
    runtimeContext: rc,
  } as never);
  console.log(JSON.stringify(backfill, null, 2));

  console.log("\n─── 2. list strategies ───");
  const strategies = await paperListStrategiesTool.execute!({
    context: {},
    runtimeContext: rc,
  } as never);
  console.log(strategies);

  console.log("\n─── 3. run backtest (SMA cross 5/20) ───");
  const report = await paperRunBacktestTool.execute!({
    context: {
      strategyId: "sma_cross",
      params: { fast_period: 5, slow_period: 20, trade_size: 0.01 },
      venue: "binance",
      symbol: "BTC/USDT",
      timeframe: "1h",
      fromTs: fromTs.toISOString(),
      toTs: toTs.toISOString(),
      initialCash: 10_000,
      feeRate: 0.001,
    },
    runtimeContext: rc,
  } as never);
  console.log(JSON.stringify(report, null, 2));

  console.log("\n─── ✅ smoke test PASSED ───");
}

main().catch((err) => {
  console.error("✗ smoke test FAILED:");
  console.error(err);
  process.exit(1);
});
