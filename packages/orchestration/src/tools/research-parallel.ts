/**
 * Parallel multi-perspective research (D-13 · P0).
 *
 * Fan-out pattern: instead of a single deep_dive call where all analyst
 * perspectives share context and cross-contaminate, run 4 independent
 * research calls in parallel — each with an isolated analyst configuration
 * targeting a specific lens (bull / bear / technical / macro).
 *
 * The 4 perspectives run concurrently. Once all complete, the raw briefs
 * and ratings are returned side-by-side so the orchestrator can synthesize
 * a balanced conclusion. Each call gets its own research_id for traceability.
 *
 * This is the first step toward the "Research Supervisor" architecture:
 * currently the 4 calls still go to the same /deep_dive endpoint, but
 * each call's internal LLM session is independent — no debate cross-talk.
 *
 * Future optimization: add a lighter-weight /deep_dive?mode=analyst-only
 * endpoint that skips the internal debate/synthesis pass (cuts cost ~40%).
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken, defaultServiceSubject } from "../auth.js";
import { ResearchClient, type ResearchPlan } from "../clients/research.js";
import { getSettings } from "../config.js";

const SymbolSchema = z.string().min(1).max(50);
const TimeframeSchema = z.enum([
  "1m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk", "1mo",
]);

/** Single-lens perspective definition. */
const PerspectiveSchema = z.object({
  /** Short label for traceability (e.g. "bull", "bear", "technical", "macro"). */
  lens: z.string(),
  /** The research question from this perspective's angle. */
  question: z.string().min(10),
});

/** Result from one parallel research lane. */
interface LaneResult {
  lens: string;
  plan: ResearchPlan;
  /** ms since lane start */
  elapsedMs: number;
}

type ToolRequestContext = { authToken?: string; get?: (key: string) => unknown };

/** Track a lane's execution with timing. */
async function runLane(
  client: ResearchClient,
  params: { venue: string; symbol: string; timeframe: string; asOf: string;
             lookbackDays: number; language?: string },
  lens: string,
  question: string,
): Promise<LaneResult> {
  const t0 = Date.now();
  const plan = await client.deepDive({
    venue: params.venue,
    symbol: params.symbol,
    timeframe: params.timeframe,
    asOf: params.asOf,
    lookbackDays: params.lookbackDays,
    userQuestion: question,
    language: params.language,
  });
  return { lens, plan, elapsedMs: Date.now() - t0 };
}

export const researchParallelDiveTool = createTool({
  id: "research.parallel_dive",
  description: `
    并行多视角研究（扇出模式）。同时对同一标的从 N 个独立视角跑深度研究，
    每个视角有独立的 LLM session —— 观点不会互相污染。

    **何时用**：
    - 用户要求多空对立的观点对比（"bull case vs bear case"）
    - 用户想要跨维度的独立分析（"技术面怎么看 + 基本面怎么看 + 宏观怎么看"）
    - 用户说"辩论一下"——把 bull/bear 分别独立跑，结论并列对比
    - 需要避免先入为主偏见时（先看到牛市分析会影响你看熊市分析的客观性）

    **何时不用**：
    - 标准研究（普通"看看 BTC 现在怎么样"）→ 用 research.deep_dive
    - 预算敏感 → 并行扇出 = N × 单次 deep_dive 成本的 LLM 调用
    - 只需要一个特定视角 → 用 research.deep_dive + userQuestion 指定方向就行

    **返回特点**：
    - lanes[] 是每个视角的完整 ResearchPlan（含独立 research_id）
    - 各视角的 rating / thesis / factors 可并列对比
    - 你需要综合 N 个视角给用户一个平衡结论，并特别指出对立观点
    - 如果有视角 rating 显著不同（如 bull=overweight vs bear=underweight），
      这本身就是信息——如实告知用户分歧在哪

    **成本**：N 倍 deep_dive（每视角一次独立 LLM 调用链）。默认最多 4 个视角。
  `.trim(),

  inputSchema: z.object({
    venue: z.string().default("binance"),
    symbol: SymbolSchema,
    timeframe: TimeframeSchema.default("1h"),
    asOf: z.string().datetime().describe("研究截止 ISO 8601"),
    lookbackDays: z.number().int().min(1).max(365).default(30),
    perspectives: z
      .array(PerspectiveSchema)
      .min(2)
      .max(4)
      .describe("至少 2 个、最多 4 个独立研究视角"),
    language: z.string().optional().describe("期望输出语言"),
  }),

  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const settings = getSettings();
    const token = tc?.authToken ?? (await mintServiceToken({ sub: defaultServiceSubject() }));
    const baseParams = {
      venue: inputData.venue ?? "binance",
      symbol: inputData.symbol,
      timeframe: inputData.timeframe ?? "1h",
      asOf: inputData.asOf,
      lookbackDays: inputData.lookbackDays ?? 30,
      language: inputData.language,
    };

    // 每个视角独立 HTTP client（避免单 client 可能的状态共享）。
    // 对于纯 HTTP，共用 client 是安全的；这里显式分开让日志/追踪更清晰。
    const lanes = inputData.perspectives.map(async (p) => {
      const client = new ResearchClient({
        baseUrl: settings.researchServiceUrl,
        token,
        timeoutMs: 300_000,
      });
      try {
        return await runLane(client, baseParams, p.lens, p.question);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return { lens: p.lens, error: msg, elapsedMs: -1 } as
          { lens: string; error: string; elapsedMs: number };
      }
    });

    const results = await Promise.all(lanes);

    const succeeded = results.filter(
      (r): r is LaneResult => "plan" in r,
    );
    const failed = results.filter(
      (r): r is { lens: string; error: string; elapsedMs: number } => "error" in r,
    );

    // Build a structured summary for the orchestrator to consume.
    const laneSummaries = succeeded.map((r) => ({
      lens: r.lens,
      research_id: r.plan.research_id,
      rating: r.plan.rating,
      confidence: r.plan.confidence,
      thesis: r.plan.thesis,
      top_risks: r.plan.risks.slice(0, 3),
      suggested_action: r.plan.suggested_action,
      elapsed_ms: r.elapsedMs,
    }));

    return {
      symbol: inputData.symbol,
      as_of: inputData.asOf,
      total_lanes: results.length,
      succeeded: succeeded.length,
      failed: failed.length,
      lanes: laneSummaries,
      // 把原始 briefs 也带回来，让 orchestator 能看到各视角的详细分析
      briefs_by_lens: Object.fromEntries(
        succeeded.map((r) => [r.lens, r.plan.briefs ?? []]),
      ),
      // 如果有失败的 lane，列出原因
      errors: failed.map((f) => ({ lens: f.lens, error: f.error })),
    };
  },
});
