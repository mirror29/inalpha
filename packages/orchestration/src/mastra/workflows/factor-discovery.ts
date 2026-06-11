/**
 * Factor discovery workflow —— L1 假设→验证强制 pipeline（D-12 · ADR-0019 简化执行）。
 *
 * 形状（ADR-0019 的 9 步坍缩为 4 个 step——hypothesis 是对话本身、formalization
 * 是 LLM 本职、lookahead_check 是 hook + 服务端解析期校验、future_return_stats
 * 与 ic_test 在 /custom/score 一次算完）：
 *
 *   validate → foreach(evaluate, { concurrency: 4 }) → bh_adjust → gate_and_propose
 *   本地审计   每条表达式打 /custom/score              批内 BH 校正   多重门 + 落候选池
 *   fail-fast                                          （m=批大小）
 *
 * 关键约束：
 *
 * - **BH 校正强制**（ADR-0019 关键约定 4）：一批试 m 个表达式，原始 p 值必须按
 *   m 做 Benjamini–Hochberg 校正——挡"试 30 个总有一个 p<0.05"的多重检验作弊
 * - **冗余剪枝**：max_corr ≥ 0.8（比服务端 snapshot 阈值 0.85 更严）的候选不 propose
 *   ——大概率是库内因子换皮
 * - **经济学故事门**：每条候选必须自带 hypothesis（schema ≥ 20 字强制）；
 *   LLM Critic 对抗审查留 L2
 * - 幸存者带 batch_id + n_tested(=批大小) + adjusted_p 落 factor_candidates
 *   （status=pending_review，**转正只能人工**——本 workflow 没有 register 路径）
 */
import { randomUUID } from "node:crypto";

import { createStep, createWorkflow } from "@mastra/core/workflows";
import { z } from "zod";

import { defaultServiceSubject, mintServiceToken } from "../../auth.js";
import { FactorClient } from "../../clients/factor.js";
import { getSettings } from "../../config.js";

// ─── schemas ───────────────────────────────────────────────────────

const TimeframeSchema = z.enum([
  "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1wk",
]);

const CandidateInputSchema = z.object({
  expression: z.string().min(2).max(2000),
  hypothesis: z
    .string()
    .min(20)
    .max(2000)
    .describe("经济学故事：为什么该有效（没有故事的表达式过不了 propose 门）"),
  name: z.string().max(120).optional(),
});

const DiscoveryInputSchema = z.object({
  candidates: z.array(CandidateInputSchema).min(1).max(10),
  venue: z.string().min(1),
  symbol: z.string().min(1).max(50),
  timeframe: TimeframeSchema.default("1h"),
  lookbackBars: z.number().int().min(120).max(10000).default(720),
  horizonBars: z.number().int().min(1).max(60).default(5),
  /** BH 校正后 p 值的 propose 门（默认 0.1——参考量级，审核人最终把关） */
  maxAdjustedP: z.number().gt(0).lte(1).default(0.1),
  /** 与库内因子 |spearman| 超过此值视为冗余（比服务端 0.85 更严） */
  maxLibraryCorr: z.number().min(0.5).max(1).default(0.8),
  /** false = 只评估打分不落候选池（dry run） */
  propose: z.boolean().default(true),
});

/** validate 透传 + 每条带回原始输入（foreach 的 item）。 */
const ValidatedItemSchema = z.object({
  candidate: CandidateInputSchema,
  venue: z.string(),
  symbol: z.string(),
  timeframe: TimeframeSchema,
  lookbackBars: z.number(),
  horizonBars: z.number(),
});

const EvaluatedItemSchema = z.object({
  candidate: CandidateInputSchema,
  /** /custom/score 的关键产物；评估失败为 null（error 给原因） */
  result: z
    .object({
      available: z.boolean(),
      rank_ic: z.number().nullable(),
      rank_ic_recent: z.number().nullable(),
      icir: z.number().nullable(),
      decay_state: z.string().nullable(),
      low_confidence: z.boolean().nullable(),
      ic_pvalue: z.number().nullable(),
      max_corr: z.number().nullable(),
      top_correlated: z.array(
        z.object({ factor_id: z.string(), corr: z.number() }),
      ),
    })
    .nullable(),
  error: z.object({ code: z.string(), message: z.string() }).nullable(),
});

const VerdictSchema = z.object({
  expression: z.string(),
  name: z.string().nullable(),
  hypothesis: z.string(),
  /** evaluated / rejected_* / proposed */
  outcome: z.enum([
    "proposed",
    "evaluated_only",
    "rejected_eval_failed",
    "rejected_low_confidence",
    "rejected_adjusted_p",
    "rejected_redundant",
    "rejected_decaying",
  ]),
  rank_ic: z.number().nullable(),
  ic_pvalue: z.number().nullable(),
  adjusted_p: z.number().nullable(),
  max_corr: z.number().nullable(),
  decay_state: z.string().nullable(),
  detail: z.string().nullable(),
  candidate_id: z.string().nullable(),
});

const DiscoveryOutputSchema = z.object({
  batch_id: z.string(),
  n_tested: z.number().int(),
  verdicts: z.array(VerdictSchema),
  summary: z.object({
    total: z.number().int(),
    proposed: z.number().int(),
    rejected: z.number().int(),
    errored: z.number().int(),
  }),
});

// ─── 纯函数：BH 校正（与服务端 effectiveness.bh_adjust 同算法）────────

/** Benjamini–Hochberg：返回与输入同序的调整后 p 值（保单调、截到 [0,1]）。 */
export function bhAdjust(pvalues: number[]): number[] {
  const m = pvalues.length;
  if (m === 0) return [];
  const order = pvalues
    .map((p, i) => [p, i] as const)
    .sort((a, b) => a[0] - b[0]);
  const ranked = order.map(([p], k) => (p * m) / (k + 1));
  for (let k = m - 2; k >= 0; k--) {
    ranked[k] = Math.min(ranked[k] ?? 1, ranked[k + 1] ?? 1);
  }
  const out = new Array<number>(m);
  order.forEach(([, idx], k) => {
    out[idx] = Math.min(1, Math.max(0, ranked[k] ?? 1));
  });
  return out;
}

// ─── steps ─────────────────────────────────────────────────────────

// 服务端是真审计；这里只做 fail-fast 的廉价整批拦截（同 factor-expression-audit
// hook 的判据）——批里混一条明显违例就别浪费其余 9 条的评估算力。
const NEGATIVE_LAG = /\b(Ref|Delta)\s*\(\s*[^,)]*,\s*-\s*\d/;
const FUTURE_NAMING = /future|ahead|tomorrow|next_|will_/i;

const validateStep = createStep({
  id: "validate",
  inputSchema: DiscoveryInputSchema,
  outputSchema: z.array(ValidatedItemSchema),
  execute: async ({ inputData }) => {
    const seen = new Set<string>();
    const out: z.infer<typeof ValidatedItemSchema>[] = [];
    for (const c of inputData.candidates) {
      if (NEGATIVE_LAG.test(c.expression)) {
        throw new Error(
          `discovery batch invalid (fail-fast): "${c.expression}" 含负 lag = lookahead。` +
            "整批拒绝——修掉这条再重提",
        );
      }
      if (FUTURE_NAMING.test(c.expression)) {
        throw new Error(
          `discovery batch invalid (fail-fast): "${c.expression}" 含未来语义命名。` +
            "因子只能由历史 bar 构成",
        );
      }
      if (seen.has(c.expression)) continue; // 同批重复表达式合并
      seen.add(c.expression);
      out.push({
        candidate: c,
        venue: inputData.venue,
        symbol: inputData.symbol,
        timeframe: inputData.timeframe,
        lookbackBars: inputData.lookbackBars,
        horizonBars: inputData.horizonBars,
      });
    }
    return out;
  },
});

const evaluateStep = createStep({
  id: "evaluate",
  inputSchema: ValidatedItemSchema,
  outputSchema: EvaluatedItemSchema,
  execute: async ({ inputData }) => {
    const settings = getSettings();
    const token = await mintServiceToken({ sub: defaultServiceSubject() });
    const client = new FactorClient({
      baseUrl: settings.factorServiceUrl,
      token,
    });
    try {
      const r = await client.customScore({
        expression: inputData.candidate.expression,
        name: inputData.candidate.name,
        venue: inputData.venue,
        symbol: inputData.symbol,
        timeframe: inputData.timeframe,
        lookbackBars: inputData.lookbackBars,
        horizonBars: inputData.horizonBars,
      });
      return {
        candidate: inputData.candidate,
        result: {
          available: r.available,
          rank_ic: r.factor?.rank_ic ?? null,
          rank_ic_recent: r.factor?.rank_ic_recent ?? null,
          icir: r.factor?.icir ?? null,
          decay_state: r.factor?.decay_state ?? null,
          low_confidence: r.factor?.low_confidence ?? null,
          ic_pvalue: r.ic_pvalue,
          max_corr: r.max_corr,
          top_correlated: r.top_correlated,
        },
        error: null,
      };
    } catch (e: unknown) {
      const code =
        e && typeof e === "object" && "code" in e
          ? String((e as { code: unknown }).code)
          : "UNKNOWN";
      const message = e instanceof Error ? e.message : String(e);
      return { candidate: inputData.candidate, result: null, error: { code, message } };
    }
  },
});

/**
 * gate_and_propose：BH 校正（m = 批大小，**含评估失败的**——失败也是一次尝试）
 * → 多重门 → 幸存者落候选池。
 *
 * foreach 没法拿到 workflow 原始 input，门限参数经 ValidatedItem 透传不优雅，
 * 这里从 runtime input 取不到 → 用 getInitData。
 */
const gateStep = createStep({
  id: "gate_and_propose",
  inputSchema: z.array(EvaluatedItemSchema),
  outputSchema: DiscoveryOutputSchema,
  execute: async ({ inputData, getInitData }) => {
    const init = getInitData<z.infer<typeof DiscoveryInputSchema>>();
    const batchId = randomUUID();
    const nTested = inputData.length;

    // BH：只对拿到 p 值的项校正，但 m 用整批大小（失败的尝试也计入选择效应背景）
    const withP = inputData.filter(
      (it): it is typeof it & { result: { ic_pvalue: number } } =>
        typeof it.result?.ic_pvalue === "number",
    );
    const rawPs = withP.map((it) => it.result.ic_pvalue);
    const m = nTested;
    // bhAdjust 内部按 m=rawPs.length 校正；线性缩放到 m=nTested（min 与正常数缩放可交换，
    // 等价于直接用 m=nTested 跑 BH）——评估失败的尝试也计入选择效应背景
    const adjusted = bhAdjust(rawPs).map((p) =>
      Math.min(1, (p * m) / Math.max(1, rawPs.length)),
    );
    const adjustedByExpr = new Map<string, number>();
    withP.forEach((it, i) =>
      adjustedByExpr.set(it.candidate.expression, adjusted[i] ?? 1),
    );

    const settings = getSettings();
    const token = await mintServiceToken({ sub: defaultServiceSubject() });
    const client = new FactorClient({ baseUrl: settings.factorServiceUrl, token });

    const verdicts: z.infer<typeof VerdictSchema>[] = [];
    for (const it of inputData) {
      const base = {
        expression: it.candidate.expression,
        name: it.candidate.name ?? null,
        hypothesis: it.candidate.hypothesis,
        rank_ic: it.result?.rank_ic ?? null,
        ic_pvalue: it.result?.ic_pvalue ?? null,
        adjusted_p: adjustedByExpr.get(it.candidate.expression) ?? null,
        max_corr: it.result?.max_corr ?? null,
        decay_state: it.result?.decay_state ?? null,
        candidate_id: null as string | null,
        detail: null as string | null,
      };
      if (it.error || !it.result?.available) {
        verdicts.push({
          ...base,
          outcome: "rejected_eval_failed",
          detail: it.error?.message ?? "evaluation unavailable",
        });
        continue;
      }
      if (it.result.low_confidence) {
        verdicts.push({
          ...base,
          outcome: "rejected_low_confidence",
          detail: "样本不足（low_confidence），换更长 lookback / 更低频再试",
        });
        continue;
      }
      if (it.result.decay_state === "decaying") {
        verdicts.push({
          ...base,
          outcome: "rejected_decaying",
          detail: "近期 IC 反号/趋零——刚提出就在衰减的因子不收",
        });
        continue;
      }
      if (it.result.max_corr !== null && it.result.max_corr >= init.maxLibraryCorr) {
        const hit = it.result.top_correlated[0]?.factor_id ?? "?";
        verdicts.push({
          ...base,
          outcome: "rejected_redundant",
          detail: `与库内 ${hit} 的 |spearman|=${it.result.max_corr.toFixed(3)} ≥ ${init.maxLibraryCorr}（换皮）`,
        });
        continue;
      }
      const adjP = adjustedByExpr.get(it.candidate.expression);
      if (adjP === undefined || adjP > init.maxAdjustedP) {
        verdicts.push({
          ...base,
          outcome: "rejected_adjusted_p",
          detail: `BH 校正后 p=${adjP?.toFixed(4) ?? "n/a"} > ${init.maxAdjustedP}（m=${m}）`,
        });
        continue;
      }
      if (!init.propose) {
        verdicts.push({ ...base, outcome: "evaluated_only" });
        continue;
      }
      try {
        const proposed = await client.proposeCandidate({
          expression: it.candidate.expression,
          hypothesis: it.candidate.hypothesis,
          name: it.candidate.name,
          venue: init.venue,
          symbol: init.symbol,
          timeframe: init.timeframe,
          testResults: {
            rank_ic: it.result.rank_ic,
            rank_ic_recent: it.result.rank_ic_recent,
            icir: it.result.icir,
            decay_state: it.result.decay_state,
            max_corr: it.result.max_corr,
            ic_pvalue: it.result.ic_pvalue,
            adjusted_p: adjP,
          },
          batchId,
          nTested,
        });
        verdicts.push({
          ...base,
          outcome: "proposed",
          candidate_id: proposed.candidate_id,
          detail: proposed.created ? null : "同表达式已有候选（幂等返老行）",
        });
      } catch (e: unknown) {
        verdicts.push({
          ...base,
          outcome: "rejected_eval_failed",
          detail: `propose failed: ${e instanceof Error ? e.message : String(e)}`,
        });
      }
    }

    const proposed = verdicts.filter((v) => v.outcome === "proposed").length;
    const errored = verdicts.filter((v) => v.outcome === "rejected_eval_failed").length;
    return {
      batch_id: batchId,
      n_tested: nTested,
      verdicts,
      summary: {
        total: nTested,
        proposed,
        rejected: nTested - proposed - errored,
        errored,
      },
    };
  },
});

// ─── workflow ──────────────────────────────────────────────────────

export const factorDiscoveryWorkflow = createWorkflow({
  id: "factor_discovery",
  inputSchema: DiscoveryInputSchema,
  outputSchema: DiscoveryOutputSchema,
})
  .then(validateStep)
  .foreach(evaluateStep, { concurrency: 4 })
  .then(gateStep)
  .commit();

export { DiscoveryInputSchema, DiscoveryOutputSchema };
