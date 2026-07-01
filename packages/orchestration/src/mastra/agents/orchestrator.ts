/**
 * Orchestrator agent —— D-8a' 简化形态（取消 supervisor pattern，直接挂全部 tool）。
 *
 * **架构演变**：
 *
 * - D-7：单 agent + 全部 tool（粗放）
 * - D-8a：supervisor pattern + trader / risk subagent（角色对抗，但 4 跳 LLM call 慢）
 * - **D-8a'（当前）**：单 agent + 全部 tool，**通过 plan store + permissions 保证安全护栏**
 * - **D-13（当前 PR）**：prompt 分层注入 —— 878 行平铺字符串 → 7 个可组合模块，
 *   按稳定性排序（STABLE 前缀 → cache 友好，VOLATILE 尾部 → 每 turn 新鲜）
 *
 * 关键约束（**不变**）：
 *
 * - LLM 没有 ``paper.submit_order_intent`` 路径（permissions/defaults.ts deny）
 * - ``trade.execute_plan`` 必须带有效 ``approvalToken``（plan store 强制）
 * - ``approval_token`` 一次性 + 短 TTL（plan store 强制）
 * - ``rationale`` 必填（plan store 强制）
 *
 * 这些约束都是**数据层强制**而不是**prompt 自律**，所以即使去掉 trader/risk 角色对抗的 prompt 也不影响安全。
 *
 * 性能收益：单 turn 内下单 = 3 个 tool call（plan→approve→execute），不再嵌套 LLM。
 *
 * == Prompt 模块结构（D-13）==
 *
 * ```
 * instructions/
 * ├── index.ts        — buildInstructions(): 按稳定性分层组合全部模块
 * ├── language.ts     — STABLE  · 输出语言规则（最高优先级）
 * ├── tool-catalog.ts — STABLE  · 工具目录 + 描述（~170 行）
 * ├── pipeline.ts     — STABLE  · 研究决策链路 + 质量门 + 迭代纪律（~220 行）
 * ├── strategy.ts     — STABLE  · 下单流 + 策略协议 + 审批门（~130 行）
 * ├── market.ts       — MARKET  · venue 路由 + 多空 + 时效性 + 归因 + 批量回测（~200 行）
 * ├── style.ts        — STABLE  · 页面上下文 + 语言风格 + 术语翻译表（~100 行）
 * └── divination.ts   — COND    · 狐神签规则（~30 行）
 * ```
 *
 * 分层排序策略（OpenHands/LangGraph 验证过的模式）：
 * STABLE 在 prompt 最前面 → Anthropic prompt cache 持续命中。
 * MARKET/动态段在尾部 → 只有尾巴变化，缓存命中率 >90%。
 */
import "../../env.js"; // side-effect: dotenv 加载根 .env（必须在 buildLLM 之前）
import { Agent } from "@mastra/core/agent";
import { TokenLimiterProcessor } from "@mastra/core/processors";

import { buildLLM } from "../llm/provider.js";
import { sharedMemory } from "../memory.js";
import {
  createPaperPendingPlanFetcher,
  createPendingPlanNoticeProcessor,
} from "../../hooks/index.js";
import { buildInstructions } from "./instructions/index.js";
import { loadWiredMcpTools, wiredOrchestratorTools } from "../wired-tools.js";

export const orchestrator = new Agent({
  id: "orchestrator",
  name: "orchestrator",
  // D-13：prompt 分层注入 —— buildInstructions() 按稳定性排序组合全部模块
  instructions: buildInstructions,
  model: buildLLM(),
  // D-8a'：不挂 subagent，全部能力 tool 化直接调
  // D-10（ADR-0009）：tools 用 dynamic 函数——静态内置 tool + 可插拔 MCP tool 合并。
  // MCP 加载是异步且 memoize 的；全挂时 loadWiredMcpTools 返空数组，不影响内置 tool。
  tools: async () => {
    const mcpTools = await loadWiredMcpTools();
    return Object.fromEntries(
      [...wiredOrchestratorTools, ...mcpTools].map((t) => [t.id, t]),
    );
  },
  memory: sharedMemory,
  // 上下文 token 兜底（2026-06-11 事故：单线程消息历史滚到 1.3M token 撑爆
  // DeepSeek 1M 上限 → INCOMPLETE_STREAM、线程报废）。tool 层已对已知大输出
  // 降采样/截断，这里是**第二道防线**：消息历史超预算时从最旧裁起（保 system），
  // processInputStep 在多步 tool loop 中每步修剪，防单 turn 内滚雪球。
  // 500k ≈ 模型上限（1M）的一半——历史预算给足，剩余一半留给
  // instructions / tool schema / 召回注入 / 输出。tool 输出已限幅后，
  // 500k ≈ 几十轮深度研究对话，正常使用几乎摸不到。
  inputProcessors: [
    new TokenLimiterProcessor({ limit: 500_000, trimMode: "contiguous" }),
  ],
  // issue #65 / ADR-0010 §Stop hook：chat 路径的 pending plan 残留警示。
  // Mastra 1.36 无"turn 结束后强制续 loop"钩子位，chat 侧降级为输出警示
  // （追加到最终回复，用户与下一 turn 的 LLM 都能看见）；真·强制续 turn
  // 在 scheduler runner（我们自己持有 generate 循环）实现。
  outputProcessors: [
    createPendingPlanNoticeProcessor({ fetcher: createPaperPendingPlanFetcher() }),
  ],
  defaultOptions: {
    // 40 步：skill 驱动的深度调研（serenity 类）一轮要 2 次 skill.read +
    // 10+ 次搜索 + 逐源 fetch + 财务核验，旧上限 15 连搜索都不够（ADR-0046 follow-up）。
    // 普通对话不受影响——maxSteps 是上限不是配额。
    maxSteps: 40,
  },
});
