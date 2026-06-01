/**
 * services/research 的 Mastra tool 包装。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken } from "../auth.js";
import { ResearchClient } from "../clients/research.js";
import { getSettings } from "../config.js";

// D-9 multi-market：与 tools/data.ts 保持一致。
const TimeframeSchema = z.enum([
  "1m", "5m", "15m", "30m", "1h", "4h",
  "1d", "1wk", "1mo", "1q", "1y",
]);

const SymbolSchema = z
  .string()
  .min(1)
  .max(50)
  .regex(
    /^[\^A-Za-z0-9._/-]+$/,
    "symbol 不能为空 / 含空格；支持 crypto 'BTC/USDT' / 普通 'AAPL' / 指数 '^N225' / akshare 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
  );

type ToolRequestContext = { authToken?: string };

async function getClient(ctx?: ToolRequestContext): Promise<ResearchClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: "service:orchestration" }));
  return new ResearchClient({ baseUrl: settings.researchServiceUrl, token });
}

// ────────────────────────────────────────────────────────────────────
// research.deep_dive
// ────────────────────────────────────────────────────────────────────

export const researchDeepDiveTool = createTool({
  id: "research.deep_dive",
  description: `
    跑一次完整的多 analyst 研究 → 综合 ResearchPlan（TradingAgents 风格）。
     同步调用，单次 90-240s（depends on market, data sources, LLM provider）。

    何时用：
    - 用户问 "BTC 现在能不能买" / "这个币现在什么观点"
    - 准备下单前先做研究（trader 之前的"建议依据"）
    - 调参 / 复盘想看 analyst 视角对照

    何时不用：
    - 单纯查 K 线走势 → data.get_bars
    - 用户已经明确要某个具体动作（"开 0.001 BTC 多单"）→ 直接走 trader 创建 plan
    - 高频 / loop 内调用 —— 单次成本 3 次 LLM 调用，**不要在 N 个标的上循环**

    返回字段:
    - research_id: UUID，本次研究的唯一标识；**透传给 paper.run_backtest / paper.list_backtest_runs / trade.create_plan 建立血缘**
    - rating: overweight / neutral / underweight
    - confidence: 0-1
    - thesis: 3-5 句核心论点（人类可读）
    - risks: 主要风险点（给 risk agent 提示重点）
    - suggested_action: 给 trader 的执行建议（"open_long 0.02 with stop below X"）
    - factors: 结构化影响因子列表（kind / value / strength / horizon / explanation）
    - signals: 因子合成的方向性信号（direction / strength / timeframe / derived_from）
    - **strategy_hint**: { family, params, reasoning }——**机器消费**，喂给 paper.compose_strategy
    - briefs: 原始 analyst briefs（technical + fundamental + sentiment）
    - horizon: intraday / swing / position 持仓周期

    典型链路：deep_dive → compose_strategy(strategy_hint, factors) → run_backtest(strategyId, params, researchId)
    → list_backtest_runs(researchId) 看历史 → create_plan(researchId, backtestRunId)

    坑：
    - asOf 必须是 ISO 8601 字符串；建议传 "现在 - 1 分钟" 避免 K 线还没落库
    - lookbackDays 建议 7-30；> 90 LLM context 容易截断
    - userQuestion 可选，但带上用户原话能让 manager 综合更贴合需求
  `.trim(),
  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    asOf: z
      .string()
      .datetime()
      .describe("研究截止时间（ISO 8601）；analyst 只看 asOf 之前数据"),
    lookbackDays: z
      .number()
      .int()
      .min(1)
      .max(365)
      .default(30)
      .describe("拉历史窗口长度（天）"),
    userQuestion: z
      .string()
      .optional()
      .describe("用户原始问题，给 research manager 综合时作 context"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.deepDive({
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackDays: inputData.lookbackDays ?? 30,
      userQuestion: inputData.userQuestion,
    });
  },
});

export const researchTools = [researchDeepDiveTool] as const;
