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
    并行批量回测（**策略 × 标的** 笛卡尔积）。

    何时用：
    - 用户提"用 A / B / C 策略在 BTC / ETH / SOL 上跑 2024 回测"这类批量需求
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
    - symbols：交易对数组（CCXT 风格 'BASE/QUOTE'），1-8 个

    坑：
    - (strategies + candidateIds) × symbols ≤ 20（grid-size-cap hook deny 超出请求）
    - 单 job CPU 上限 3 分钟（服务端 RLIMIT_CPU）
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
