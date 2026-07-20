/**
 * Factor evolution workflow —— P3 · 因子演化闭环（ADR-0055）。
 *
 * 当 factor.evaluate_candidate 发现 IC 有潜力但未过门限时，此 workflow 自动
 * 生成 2-3 个改进变体，并行评估，选最优，迭代最多 N 轮。
 *
 * 形状：
 *
 *   hypothesis (原始表达式)
 *     → evaluate (调 /custom/score)
 *     → gate (IC 有潜力但未达标?)
 *     → evolve (LLM 生成 2-3 变体)
 *     → evaluate_variants (并行评估)
 *     → select_best (保留最优)
 *     → loop (最多 N 轮, 或收敛)
 *     → persist (演化链落盘)
 */
import { createStep, createWorkflow } from "@mastra/core/workflows";
import { z } from "zod";

import { defaultServiceSubject, mintServiceToken } from "../../auth.js";
import { FactorClient } from "../../clients/factor.js";
import { getSettings } from "../../config.js";

// ─── schemas ───────────────────────────────────────────────────────

const EvolutionInputSchema = z.object({
  /** 原始表达式 */
  expression: z.string().min(2).max(2000),
  name: z.string().max(120).optional(),
  venue: z.string().min(1),
  symbol: z.string().min(1).max(50),
  timeframe: z.string().default("1h"),
  lookbackBars: z.number().int().min(120).max(10000).default(720),
  horizonBars: z.number().int().min(1).max(60).default(5),
  /** 原始 IC（从 evaluate_candidate 传入） */
  originalRankIc: z.number().nullable(),
  originalPvalue: z.number().nullable(),
  /** 最大演化轮数 */
  maxRounds: z.number().int().min(1).max(10).default(5),
  /** 收敛阈值：连续两轮 improvement < 此比例时停止 */
  convergenceThreshold: z.number().min(0).max(1).default(0.05),
});

const EvolutionStepSchema = z.object({
  expression: z.string(),
  rank_ic: z.number().nullable(),
  ic_pvalue: z.number().nullable(),
  /** 父表达式 hash（根为 null） */
  parent_id: z.string().nullable(),
  /** 变异描述，如 "调整窗口 20→30" */
  mutation: z.string(),
  /** 第 N 轮 */
  step: z.number().int(),
});

const EvolutionOutputSchema = z.object({
  /** 原始表达式 */
  root_expression: z.string(),
  /** 演化链 */
  steps: z.array(EvolutionStepSchema),
  /** 最终最优表达式 */
  best_expression: z.string().nullable(),
  /** 最终最优 IC */
  final_rank_ic: z.number().nullable(),
  /** 对比原始 IC 的提升百分比 */
  improvement_pct: z.number().nullable(),
  /** 总演化轮数 */
  n_rounds: z.number().int(),
  /** 是否收敛（提前停止） */
  converged: z.boolean(),
});

// ─── 演化 prompt ──────────────────────────────────────────────────

function buildEvolutionPrompt(
  expression: string,
  rankIc: number | null,
  icPvalue: number | null,
  decayState: string | null,
  maxCorr: number | null,
): string {
  return `你是一个量化因子研究员。给定以下因子表达式及其评估结果，请生成 2-3 个改进变体。

原始表达式: ${expression}
评估结果:
- Rank IC: ${rankIc ?? "N/A"}
- IC p-value: ${icPvalue ?? "N/A"}
- Decay state: ${decayState ?? "N/A"}
- Max library correlation: ${maxCorr ?? "N/A"}

改进方向（选择一个或多个）：
1. 调整窗口参数（如 Mean($close, 20) → Mean($close, 30)）
2. 更换核心算子（如 Mean → EMA, Delta → Ref 组合）
3. 添加辅助过滤条件（如用 If + Sign 做方向性过滤）
4. 组合多个子表达式（如用 And/Or 组合两个独立信号）
5. 尝试其他算子族（如从动量换到波动率）

约束：
- 只能使用白名单算子：Ref/Delta/Mean/Std/Sum/Max/Min/EMA/WMA/Corr/Rank/Quantile/Abs/Log/Sign/Greater/Less/If/Skew/Kurt/Med/Slope/IdxMax/IdxMin/Cov/And/Or/Not
- 只能引用 $close/$open/$high/$low/$volume 列
- 窗口参数必须在 1-500 之间
- Ref/Delta 的 lag 必须为正整数

请按以下格式输出每个变体：
EXPRESSION: <表达式>
MUTATION: <变异描述，如 "将窗口从 20 改为 30">
REASON: <为什么这个改法可能提升 IC>`;
}

// ─── steps ────────────────────────────────────────────────────────

/** 评估单步（调用 /custom/score） */
async function evaluateExpression(
  client: FactorClient,
  expression: string,
  name: string | undefined,
  venue: string,
  symbol: string,
  timeframe: string,
  lookbackBars: number,
  horizonBars: number,
) {
  try {
    const r = await client.customScore({
      expression,
      name,
      venue,
      symbol,
      timeframe,
      lookbackBars,
      horizonBars,
    });
    return {
      expression,
      rank_ic: r.factor?.rank_ic ?? null,
      ic_pvalue: r.ic_pvalue,
      decay_state: r.factor?.decay_state ?? null,
      max_corr: r.max_corr,
      available: r.available,
      error: null,
    };
  } catch (e: unknown) {
    return {
      expression,
      rank_ic: null,
      ic_pvalue: null,
      decay_state: null,
      max_corr: null,
      available: false,
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

const evaluateStep = createStep({
  id: "evaluate_original",
  inputSchema: EvolutionInputSchema,
  outputSchema: z.object({
    root_expression: z.string(),
    original_rank_ic: z.number().nullable(),
    original_pvalue: z.number().nullable(),
    steps: z.array(EvolutionStepSchema),
    n_rounds: z.number().int(),
    converged: z.boolean(),
  }),
  execute: async ({ inputData }) => {
    return {
      root_expression: inputData.expression,
      original_rank_ic: inputData.originalRankIc,
      original_pvalue: inputData.originalPvalue,
      steps: [
        {
          expression: inputData.expression,
          rank_ic: inputData.originalRankIc,
          ic_pvalue: inputData.originalPvalue,
          parent_id: null,
          mutation: "原始表达式",
          step: 0,
        },
      ],
      n_rounds: 0,
      converged: false,
    };
  },
});

/** 演化循环：生成变体 → 评估 → 选最优 → 迭代 */
const evolveStep = createStep({
  id: "evolve",
  inputSchema: z.object({
    root_expression: z.string(),
    original_rank_ic: z.number().nullable(),
    original_pvalue: z.number().nullable(),
    steps: z.array(EvolutionStepSchema),
    n_rounds: z.number().int(),
    converged: z.boolean(),
    venue: z.string(),
    symbol: z.string(),
    timeframe: z.string(),
    lookbackBars: z.number(),
    horizonBars: z.number(),
    maxRounds: z.number(),
    convergenceThreshold: z.number(),
  }),
  outputSchema: z.object({
    root_expression: z.string(),
    original_rank_ic: z.number().nullable(),
    original_pvalue: z.number().nullable(),
    steps: z.array(EvolutionStepSchema),
    best_expression: z.string().nullable(),
    final_rank_ic: z.number().nullable(),
    improvement_pct: z.number().nullable(),
    n_rounds: z.number().int(),
    converged: z.boolean(),
  }),
  execute: async ({ inputData }) => {
    const settings = getSettings();
    const token = await mintServiceToken({ sub: defaultServiceSubject() });
    const client = new FactorClient({ baseUrl: settings.factorServiceUrl, token });

    let steps = [...inputData.steps];
    let nRounds = inputData.n_rounds;
    let converged = inputData.converged;
    let bestExpr = steps.reduce((best, s) =>
      (s.rank_ic !== null && Math.abs(s.rank_ic) > Math.abs(best.rank_ic ?? 0)) ? s : best,
    );
    let prevBestIc = Math.abs(bestExpr.rank_ic ?? 0);

    while (nRounds < inputData.maxRounds && !converged) {
      // 取当前最佳作为演化基础
      const currentExpr = steps.reduce((best, s) =>
        (s.rank_ic !== null && Math.abs(s.rank_ic) > Math.abs(best.rank_ic ?? 0)) ? s : best,
      );

      // 如果当前最佳是根且 IC 不可用，跳过
      if (currentExpr.rank_ic === null) break;

      // LLM 生成变体（通过 Mastra agent 调用）
      const prompt = buildEvolutionPrompt(
        currentExpr.expression,
        currentExpr.rank_ic,
        currentExpr.ic_pvalue,
        null, // decay_state
        null, // max_corr
      );

      // 这里用简单的模式生成变体（避免依赖 LLM agent 调用）
      // 实际产品中应通过 Mastra agent 调用 LLM
      const variants = generateVariants(currentExpr.expression);

      if (variants.length === 0) break;

      // 并行评估变体
      const results = await Promise.all(
        variants.map((v) =>
          evaluateExpression(
            client,
            v.expression,
            undefined,
            inputData.venue,
            inputData.symbol,
            inputData.timeframe,
            inputData.lookbackBars,
            inputData.horizonBars,
          ).then((r) => ({ ...r, mutation: v.mutation })),
        ),
      );

      // 记录演化步骤
      for (const r of results) {
        steps.push({
          expression: r.expression,
          rank_ic: r.rank_ic,
          ic_pvalue: r.ic_pvalue,
          parent_id: currentExpr.expression,
          mutation: r.mutation,
          step: nRounds + 1,
        });
      }

      nRounds++;

      // 检查收敛
      const newBest = steps.reduce((best, s) =>
        (s.rank_ic !== null && Math.abs(s.rank_ic) > Math.abs(best.rank_ic ?? 0)) ? s : best,
      );
      const newBestIc = Math.abs(newBest.rank_ic ?? 0);

      if (prevBestIc > 0 && (newBestIc - prevBestIc) / prevBestIc < inputData.convergenceThreshold) {
        converged = true;
      }
      prevBestIc = newBestIc;
    }

    // 计算结果
    const finalBest = steps.reduce((best, s) =>
      (s.rank_ic !== null && Math.abs(s.rank_ic) > Math.abs(best.rank_ic ?? 0)) ? s : best,
    );
    const originalIc = Math.abs(inputData.original_rank_ic ?? 0);
    const improvementPct =
      originalIc > 0 && finalBest.rank_ic !== null
        ? (Math.abs(finalBest.rank_ic) - originalIc) / originalIc
        : null;

    return {
      root_expression: inputData.root_expression,
      original_rank_ic: inputData.original_rank_ic,
      original_pvalue: inputData.original_pvalue,
      steps,
      best_expression: finalBest.expression,
      final_rank_ic: finalBest.rank_ic,
      improvement_pct: improvementPct,
      n_rounds: nRounds,
      converged,
    };
  },
});

/**
 * 简单的变体生成器：基于常见模式生成候选变体。
 *
 * 产品环境应替换为 LLM agent 调用，这里用确定性规则保证 workflow 可测试。
 */
function generateVariants(expression: string): { expression: string; mutation: string }[] {
  const variants: { expression: string; mutation: string }[] = [];

  // 检测并调整窗口参数
  const windowMatch = expression.match(/(Mean|Std|Sum|Max|Min|EMA|WMA|Rank|Skew|Kurt|Med|Slope)\(([^,]+),\s*(\d+)\)/);
  if (windowMatch) {
    const [full, op, arg, win] = windowMatch;
    const w = parseInt(win, 10);
    if (w > 5) {
      // 缩小窗口
      variants.push({
        expression: expression.replace(full, `${op}(${arg}, ${Math.max(2, Math.floor(w / 2))})`),
        mutation: `${op} 窗口 ${w}→${Math.max(2, Math.floor(w / 2))}`,
      });
    }
    if (w < 400) {
      // 放大窗口
      variants.push({
        expression: expression.replace(full, `${op}(${arg}, ${Math.min(500, w * 2)})`),
        mutation: `${op} 窗口 ${w}→${Math.min(500, w * 2)}`,
      });
    }
  }

  // 检测 Mean → EMA 替换
  if (expression.includes("Mean(")) {
    const meanMatch = expression.match(/Mean\(([^,]+),\s*(\d+)\)/);
    if (meanMatch) {
      variants.push({
        expression: expression.replace(meanMatch[0], `EMA(${meanMatch[1]}, ${meanMatch[2]})`),
        mutation: `Mean→EMA，窗口 ${meanMatch[2]}`,
      });
    }
  }

  // 检测 Delta → Ref 替换
  if (expression.includes("Delta(")) {
    const deltaMatch = expression.match(/Delta\(([^,]+),\s*(\d+)\)/);
    if (deltaMatch) {
      const [full, arg, lag] = deltaMatch;
      variants.push({
        expression: expression.replace(full, `(${arg} - Ref(${arg}, ${lag}))`),
        mutation: `Delta→显式 Ref 减法，lag=${lag}`,
      });
    }
  }

  return variants;
}

// ─── workflow ──────────────────────────────────────────────────────

export const factorEvolutionWorkflow = createWorkflow({
  id: "factor_evolution",
  inputSchema: EvolutionInputSchema,
  outputSchema: EvolutionOutputSchema,
})
  .then(evaluateStep)
  .then(evolveStep)
  .commit();

export { EvolutionInputSchema, EvolutionOutputSchema };