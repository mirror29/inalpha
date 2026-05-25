/**
 * Tool 注册聚合。
 *
 * - D-7 起步：data + paper 5 个
 * - D-8a：trade-plan 5 个（create / approve / reject / execute / get）
 * - D-8b：research 1 个（deep_dive）
 */
import {
  dataBackfillBarsTool,
  dataGetBarsTool,
  dataGetTickerTool,
  dataTools,
} from "./data.js";
import {
  paperComposeStrategyTool,
  paperGetAccountTool,
  paperHealthTool,
  paperListBacktestRunsTool,
  paperListOrdersTool,
  paperListPositionsTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
  paperTools,
} from "./paper.js";
import { researchDeepDiveTool, researchTools } from "./research.js";
import {
  schedulerCreateJobTool,
  schedulerGetJobTool,
  schedulerListJobsTool,
  schedulerListRunsTool,
  schedulerSetEnabledTool,
  schedulerTools,
  schedulerTriggerJobTool,
} from "./scheduler.js";
import { swarmRunBacktestGridTool, swarmTools } from "./swarm.js";
import {
  approveTradePlanTool,
  createTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
  rejectTradePlanTool,
  tradePlanTools,
} from "./trade-plan.js";

export {
  approveTradePlanTool,
  createTradePlanTool,
  dataBackfillBarsTool,
  dataGetBarsTool,
  dataGetTickerTool,
  executeTradePlanTool,
  getTradePlanTool,
  paperComposeStrategyTool,
  paperGetAccountTool,
  paperHealthTool,
  paperListBacktestRunsTool,
  paperListOrdersTool,
  paperListPositionsTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
  rejectTradePlanTool,
  researchDeepDiveTool,
  schedulerCreateJobTool,
  schedulerGetJobTool,
  schedulerListJobsTool,
  schedulerListRunsTool,
  schedulerSetEnabledTool,
  schedulerTriggerJobTool,
  swarmRunBacktestGridTool,
};

/** 所有 tool 数组，给 Mastra Agent 直接挂载。 */
export const allTools = [
  ...dataTools,
  ...paperTools,
  ...tradePlanTools,
  ...researchTools,
  ...swarmTools,
  ...schedulerTools,
] as const;

/** 给 trader subagent 用（不含 risk 的 approve/reject）。 */
export const traderTools = [
  dataGetBarsTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
  createTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
] as const;

/** 给 risk subagent 用（只有审批 + 查询）。 */
export const riskTools = [
  getTradePlanTool,
  approveTradePlanTool,
  rejectTradePlanTool,
] as const;

/**
 * orchestrator 用的 tool 列表（D-8a' 形态：取消 trader/risk subagent，直接挂全部 tool）。
 *
 * **架构演变**：
 *
 * - D-8a：通过 subagent 隔离（trader 只有 create/execute，risk 只有 approve/reject）
 * - D-8a'：废弃 subagent 嵌套（**性能**：3 个嵌套 LLM call → 直接 3 个 tool call；
 *   **同一性**：plan/approve/execute 本就是流程而不是 agent，更适合 tool 序列）。
 *
 * 安全护栏不变：
 * - LLM 没有 paper.submit_order 路径（permissions deny）
 * - approval_token 一次性 + 短 TTL（plan store 强制）
 * - rationale 必填（plan store 强制）
 *
 * 后续 D-9：risk 升级为 deterministic 规则 + 复杂场景才升级到 LLM call。
 */
export const orchestratorToolList = [
  // 数据 / 回测 / 健康
  dataGetBarsTool,
  dataBackfillBarsTool,
  dataGetTickerTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
  paperHealthTool,
  // 研究
  researchDeepDiveTool,
  // D-8c 研究→策略 链路（compose 路由 + 历史回测查询）
  paperComposeStrategyTool,
  paperListBacktestRunsTool,
  // ADR-0025 Swarm S1：并行批量回测
  swarmRunBacktestGridTool,
  // Plan/Exec 五件套（D-8a' 直接挂到 orchestrator）
  createTradePlanTool,
  approveTradePlanTool,
  rejectTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
  // D-8b 用户回溯查询
  paperListOrdersTool,
  paperListPositionsTool,
  paperGetAccountTool,
  // D-9 类 Hermes 定时管理（让 agent 在对话里跑 scheduler）
  schedulerCreateJobTool,
  schedulerListJobsTool,
  schedulerGetJobTool,
  schedulerSetEnabledTool,
  schedulerTriggerJobTool,
  schedulerListRunsTool,
] as const;

/** 名字 → tool 索引，给 framework 路由用。 */
export const toolMap = Object.fromEntries(allTools.map((t) => [t.id, t]));
