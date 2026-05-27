/**
 * Swarm tools —— 并行批量回测 / 研究的入口（ADR-0025 §D3 §D4）。
 *
 * 设计：LLM 只看到一个"批量回测"tool，扇出 / 并发 / 聚合全藏在 ``backtest_grid`` workflow。
 * 数据 + 流程边界：
 *
 *   tool (本文件)         →  PreToolUse grid-size-cap hook  →  workflow (`backtest_grid`)
 *   schema 校验 + 包装       grid 总数 > 20 直接 deny           expand + foreach + aggregate
 *
 * **不**支持的事情（用 paper.run_backtest 单跑就好）：
 * - 单策略单标的回测
 * - 不同 timeframe 的 grid（强制单 timeframe）
 * - 跨 venue（强制单 venue）
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { GridInputSchema, GridOutputSchema } from "../mastra/workflows/backtest-grid.js";

export const swarmRunBacktestGridTool = createTool({
  id: "swarm.run_backtest_grid",
  description: `
    并行批量回测（**策略 × 标的** 笛卡尔积）。**D-9：全市场支持**——
    crypto / 美股 / A 股 / 港股 / 全球指数 / FRED 宏观 序列都可走 grid，
    venue / symbol 形式同 paper.run_backtest（见其 description 全表）。

    何时用：
    - 用户提"用 A / B / C 策略在多个标的上跑"——任何市场任何 ticker 都可
      （crypto BTC/ETH/SOL、美股 AAPL/MSFT/NVDA、A 股 sh.600519/sz.300750、
      港股 hk.00700、指数 ^GSPC/^N225、FRED 宏观 DFF 等都行）
    - 想找 Pareto 前沿 / topK 看哪个组合 Sharpe-DD 最优
    - **D-9：N 个自创策略候选并行回测**——传 candidateIds 数组，**不要**串行调 paper.run_backtest

    何时不用：
    - **单策略单标的** → 用 paper.run_backtest（无 grid 开销）
    - **研究决策** → 用 research.deep_dive
    - **同步下单** → 用 trade.create_plan / approve / execute 三件套

    输入：
    - strategies：内置策略 ID 数组（sma_cross / buy_and_hold / mean_reversion）
    - candidateIds：D-9 自创策略候选 UUID 数组（来自 paper.author_strategy）
    - **strategies 和 candidateIds 至少一个非空；两者总数 ≤ 5**
    - symbols：标的数组（**任意 venue/格式**，1-8 个），同 grid 内所有 symbol
      应属同一 venue —— grid 不跨 venue
    - venue：跟 symbols 配套，按市场分类选（crypto→binance / 美股→yfinance 或 alpaca /
      A 股→akshare / 全球→yfinance / FRED→fred）

    坑：
    - (strategies + candidateIds) × symbols ≤ 20（grid-size-cap hook deny 超出请求）
    - 单 job CPU 上限 3 分钟（服务端 RLIMIT_CPU）
    - 非 binance venue 首次跑前可能需 data.backfill_bars（缓存缺时 paper 端会
      报 NO_BARS_AVAILABLE）
    - **不**返回 equity_curve / final_positions 等大字段；只回 summary + Pareto + topK，
      要看完整 report 单跑 paper.run_backtest

    输出：reports (每 job 一条 ok/errored) + pareto (Sharpe vs maxDD 上凸包) + top_k (top 3
    by Sharpe) + summary (total / ok / errored / wall_time_ms)

    **D-9 报告字段扩展**：candidate 路径的每条 report 含 \`candidate_id\` / \`fitness\` /
    \`baseline\`（buy_and_hold 对照），用 \`fitness > baseline.fitness\` 判 alpha
  `.trim(),
  inputSchema: GridInputSchema,
  outputSchema: GridOutputSchema,
  execute: async (inputData, ctx) => {
    const mastra = ctx?.mastra;
    if (!mastra) {
      throw new Error("swarm.run_backtest_grid: mastra ctx missing (cannot reach workflow)");
    }
    const wf = mastra.getWorkflow("backtest_grid");
    const run = await wf.createRun();
    const result = await run.start({ inputData });
    if (result.status !== "success") {
      throw result.status === "failed"
        ? result.error
        : new Error(`backtest_grid workflow status: ${result.status}`);
    }
    // outputSchema 已 narrow 过
    return result.result as z.infer<typeof GridOutputSchema>;
  },
});

/** Swarm tool 集合。 */
export const swarmTools = [swarmRunBacktestGridTool] as const;
