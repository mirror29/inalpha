/**
 * services/research 的 Mastra tool 包装。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { resolveRequestToken } from "../auth.js";
import { ResearchClient, type PersonaKey } from "../clients/research.js";
import { getSettings } from "../config.js";

// ADR-0037 §A：投资大师人格 key（与 services/research analysts/personas 对齐）。
const PersonaSchema = z.enum([
  "buffett", "lynch", "wood", "burry", "druckenmiller", "marks",
]);

// D-9 multi-market：与 tools/data.ts 保持一致。
// D-13：导出给 research-parallel.ts 复用，避免同一概念在两处独立漂移。
export const TimeframeSchema = z.enum([
  "1m", "5m", "15m", "30m", "1h", "4h",
  "1d", "1wk", "1mo", "1q", "1y",
]);

export const SymbolSchema = z
  .string()
  .min(1)
  .max(50)
  .regex(
    /^[\^A-Za-z0-9._/\-:]+$/,
    "symbol 不能为空 / 含空格；支持 crypto 'BTC/USDT' / 普通 'AAPL' / 指数 '^N225' / baostock 'sh.600519' / yfinance '005930.KS' / FRED 'DFF'",
  );

type ToolRequestContext = { authToken?: string; get?: (key: string) => unknown };

async function getClient(ctx?: ToolRequestContext): Promise<ResearchClient> {
  const settings = getSettings();
  const token = await resolveRequestToken(ctx);
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

    可选 personas（投资大师视角，ADR-0037 §A）：
    - 传 personas 时，核心 analyst 之外再叠加对应投资大师风格视角（buffett 价值/护城河、
      lynch GARP 成长、wood 颠覆创新、burry 逆向/泡沫、druckenmiller 宏观趋势、marks 周期/风险），
      形成"大师团"多视角 + 对立观点，喂进辩论与综合
    - 何时用：用户想要"不同投资风格怎么看这个标的""价值派 vs 成长派会怎么吵"这类多视角对照
    - 何时不用：普通研究不必带（每个 persona 多一次 LLM 调用、成本线性上升）；不确定就省略
    - 省略 = 只跑核心 analyst（行为与历史一致）

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
    personas: z
      .array(PersonaSchema)
      .optional()
      .describe(
        "可选：额外启用的投资大师人格视角（每个多一次 LLM 调用）。" +
          "想要多投资风格 / 对立观点对照时传；普通研究省略",
      ),
    language: z
      .string()
      .optional()
      .describe(
        "期望输出语言（自然语言名，如 'English' / '中文'）。**应设为用户最近一条消息的语言**，" +
          "让 analyst / 多空辩论 / 综合结论直接用该语言返回；省略则随模型默认（可能不符）",
      ),
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
      personas: inputData.personas as PersonaKey[] | undefined,
      language: inputData.language,
    });
  },
});

export const researchTools = [researchDeepDiveTool] as const;
