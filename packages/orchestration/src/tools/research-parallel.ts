/**
 * Parallel multi-hint research fan-out (D-13 · P0).
 *
 * 对同一标的**并行跑 N 次完整 deep_dive**，每次带不同侧重的 userQuestion（hint）。
 * 每次调用是独立的 HTTP 请求 + 独立 research_id，彼此不共享进程内状态——
 * 但**每次仍走后端完整的 run_deep_dive**（6 个 analyst + Bull/Bear 辩论 + manager 综合），
 * 后端没有 "lens" 概念，不会按 hint 裁剪 analyst 集合。
 *
 * ⚠️ **能力边界（不要夸大）**：这不是"bull-only / bear-only 的独立视角推理"——
 * 4 条 lane 在相同 venue/symbol/timeframe/asOf 下只是提问措辞不同，本质是
 * **同一证据链的 N 次带侧重采样**（成本 = N × deep_dive）。收益是：
 *   1. 采样多样性——不同提问角度可能触发不同的 analyst 强调点
 *   2. 独立 research_id 便于分别溯源
 *   3. 并行执行省墙钟时间
 * 它**不能**保证"多空分歧"是真实的市场分歧——可能只是 LLM 采样噪声。
 * orchestrator 呈现结果时应措辞为"从不同提问角度看"，而非"客观独立结论"。
 *
 * 未来真正的视角隔离需要 server 端支持 mode=analyst-only + 按 lens 过滤
 * analyst 集合（跳过全量辩论），届时才是真正的"Research Supervisor"架构。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken, defaultServiceSubject } from "../auth.js";
import { ResearchClient, type ResearchPlan } from "../clients/research.js";
import { getSettings } from "../config.js";
// D-13：复用 research.ts 的同一份 schema（含正则校验），避免两处独立漂移。
import { SymbolSchema, TimeframeSchema } from "./research.js";

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
    并行多提问研究（扇出模式）。对同一标的**并行跑 N 次完整 deep_dive**，
    每次带不同侧重的提问（如偏多头 / 偏空头 / 偏技术 / 偏宏观）。

    ⚠️ **能力边界（呈现给用户时务必如实）**：每条 lane 都是后端完整的 deep_dive
    （同一套 6 analyst + Bull/Bear 辩论），只是 userQuestion 措辞不同——**不是**
    bull-only / bear-only 的独立视角推理。4 条 lane 在相同 venue/symbol/timeframe
    下本质是**同一证据链的 N 次带侧重采样**。呈现结果时措辞用"从不同提问角度看"，
    **不要**说成"客观独立结论"；rating 分歧可能只是 LLM 采样噪声，不等于真实市场分歧。

    **何时用**：
    - 用户明确要"多空对比 / 换几个角度看看 / 辩论一下"——想要提问多样性的采样
    - 想同时拿到几个不同侧重的完整研究报告并列对比

    **何时不用**：
    - 标准研究（普通"看看 BTC 现在怎么样"）→ 用 research.deep_dive
    - 预算敏感 → 这是 N × deep_dive 成本
    - 想要单一方向 → 用 research.deep_dive + userQuestion 指定就行
    - 想让"多空分歧"当作客观信号 → 它给不了这个保证（见上边界说明）

    **返回特点**：
    - lanes[] 是每次提问的完整 ResearchPlan（含独立 research_id）
    - 各 lane 的 rating / thesis / factors 可并列对比
    - 综合时给用户一个平衡结论，如实标注"这是不同提问角度的采样，非独立客观结论"

    **成本**：N 倍 deep_dive（每 lane 一次完整 LLM 调用链）。默认最多 4 条。
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
