/**
 * services/evolver 的 Mastra tool 包装 —— 策略演化引擎。
 *
 * 让 orchestrator agent 在对话里能启动演化轮次、查询演化状态。
 * 这是 E2 演化闭环的用户级入口。
 *
 * Tool 设计遵循 docs/05-tool-skill-discipline.md 的"做什么 / 何时用 / 何时不用 / 坑"四要素。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { resolveRequestToken } from "../auth.js";
import { EvolverClient } from "../clients/evolver.js";
import { getSettings } from "../config.js";

type ToolRequestContext = { authToken?: string; get?: (key: string) => unknown };

async function getClient(ctx?: ToolRequestContext): Promise<EvolverClient> {
  const settings = getSettings();
  const token = await resolveRequestToken(ctx);
  return new EvolverClient({
    baseUrl: settings.evolverServiceUrl,
    token,
    // evolver 单次运行可能含 budget 个 LLM 调用 × budget 个回测，整轮可达数分钟
    timeoutMs: 600_000,
  });
}

const EvolutionConfigSchema = z
  .object({
    universe: z
      .array(z.string().min(1))
      .optional()
      .describe("标的列表，如 ['BTCUSDT']。默认 ['BTCUSDT']"),
    period_from: z
      .string()
      .optional()
      .describe("回测起始日期 YYYY-MM-DD。默认 2025-01-01"),
    period_to: z
      .string()
      .optional()
      .describe("回测截止日期 YYYY-MM-DD。默认 2025-12-31"),
    timeframe: z
      .enum(["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1wk", "1mo"])
      .optional()
      .describe("K 线周期。默认 1h"),
    initial_cash: z
      .number()
      .min(100)
      .optional()
      .describe("初始本金 USD。默认 10000"),
  })
  .optional()
  .describe("演化运行参数（可选，全部有默认值）");

// ────────────────────────────────────────────────────────────────────
// evolver.run_evolution
// ────────────────────────────────────────────────────────────────────

export const evolverRunEvolutionTool = createTool({
  id: "evolver.run_evolution",
  description: `
    启动一次策略演化运行（E2 演化引擎）。

    演化引擎会对种子策略做 N 次 LLM 驱动的代码变异，每版候选过三道沙盒
    （AST 审计 / 契约校验 / 回测评估），最终返回按 fitness 排序的候选列表。

    运行时覆盖维度（HintGenerator 循环输出）：
    - 入场上：成交量确认过滤器防假突破
    - 回撤上：跟踪止损（trailing stop）
    - 参数级：快慢线周期网格寻优
    - 风控上：最大持仓天数 + 波动率过滤器

    何时用：
    - 用户说"帮我演化策略 / 自动优化 / 变异一下 sma_cross"
    - 用户说"试试不同参数组合 / 自动帮我改进策略"
    - 手工迭代到上限后想要 LLM 探索新方向（互补：你走 pipeline 的 5 步迭代，
      evolver 走并级变异探索）
    - 用户说"看下有什么可能比现在更好的策略"

    何时不用：
    - 只想跑一次回测 → paper.run_backtest（evolver = 回测 × budget，成本高）
    - 用户已明确要自己写代码 → paper.author_strategy
    - 种子策略还没做过 baseline 评估 → 先跑一遍 paper.run_backtest 确认 baseline
      再说演化（不然变异方向没有参照系）

    输出：
    - 202 Accepted + run_id + status + 候选统计（candidates_count / rejected_ast / rejected_contract / failed_eval）
    - 用 evolver.get_evolution 轮询最终结果（含候选列表按 fitness 降序）

    坑：
    - 每次演化 = budget × (LLM 调用 + 沙盒 + 回测)，budget=4 约 2-4 分钟
    - 种子策略必须是 evolver 服务已知的 ID（默认 sma_cross_v1）
    - 候选在 evolver 内存表里（E1 过渡方案，进程重启丢失）；后续要 promote 的
      走 paper.author_strategy 重新入库才持久化
    - config 默认 BTCUSDT 1h 2025 年，要改 universe / 周期必须显式传
    - Promote 成功后 evolver 会自动以 promoted 代码为种子触发生成下一代候选
      （链路：promote → evolver hook → 新一轮演化），这会花一些 budget
  `.trim(),
  inputSchema: z.object({
    budget: z
      .number()
      .int()
      .min(1)
      .max(20)
      .default(4)
      .describe("变异预算数（产生多少候选），默认 4。最大值 20"),
    seedStrategyId: z
      .string()
      .min(1)
      .max(100)
      .default("sma_cross_v1")
      .describe("种子策略 ID，默认 sma_cross_v1。promote 后自动演化会填 candidate:<uuid>"),
    config: EvolutionConfigSchema,
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.startRun(
      inputData.budget ?? 4,
      inputData.seedStrategyId ?? "sma_cross_v1",
      inputData.config ?? undefined,
    );
  },
});

// ────────────────────────────────────────────────────────────────────
// evolver.get_evolution
// ────────────────────────────────────────────────────────────────────

export const evolverGetEvolutionTool = createTool({
  id: "evolver.get_evolution",
  description: `
    查询一次演化运行的完整状态（含候选列表，按 fitness 降序）。

    何时用：
    - evolver.run_evolution 返回后轮询拿结果
    - 用户问"上次演化的结果怎么样 / 演化完了没"

    何时不用：
    - 只想看一个候选的详情 → evolver.get_candidate

    返回字段：
    - status: "completed"（已完成）/ "running"（还在跑）/ "failed"（种子评估失败）
    - candidates_count: 通过三道沙盒的候选数
    - candidates: 候选列表（完整源码 / fitness / report / mutation_hint）
    - rejected_ast / rejected_contract / failed_eval: 各阶段的拒绝数
    - llm_cost_usd: 总 LLM 费用

    坑：
    - 候选在 evolver 内存中（E1 过渡），进程重启后查不到历史
    - 想要 promote 的候选——把它的 source_code 喂给 paper.author_strategy
      入库再 promote（evolver 本身不落策略表——那是 paper 的职责）
  `.trim(),
  inputSchema: z.object({
    runId: z
      .string()
      .min(1)
      .describe("演化运行 UUID（从 evolver.run_evolution 的返回取 run_id）"),
  }),
  execute: async (inputData, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.getRun(inputData.runId);
  },
});

export const evolverTools = [evolverRunEvolutionTool, evolverGetEvolutionTool] as const;