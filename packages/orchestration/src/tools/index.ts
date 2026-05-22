/**
 * Tool 注册聚合。
 *
 * - D-7 起步：data + paper 5 个
 * - D-8a：trade-plan 5 个（create / approve / reject / execute / get）
 * - D-8b：research 1 个（deep_dive）
 */
import { dataBackfillBarsTool, dataGetBarsTool, dataTools } from "./data.js";
import { paperHealthTool, paperListStrategiesTool, paperRunBacktestTool, paperTools } from "./paper.js";
import { researchDeepDiveTool, researchTools } from "./research.js";
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
  executeTradePlanTool,
  getTradePlanTool,
  paperHealthTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
  rejectTradePlanTool,
  researchDeepDiveTool,
};

/** 所有 tool 数组，给 Mastra Agent 直接挂载。 */
export const allTools = [
  ...dataTools,
  ...paperTools,
  ...tradePlanTools,
  ...researchTools,
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
 * orchestrator 自己用的 tool —— 路由层级 + 研究查询。**不含 trade.* 任何一个**，
 * 强制下单走 trader subagent（ADR-0012 plan/exec 隔离）。
 */
export const orchestratorToolList = [
  dataGetBarsTool,
  dataBackfillBarsTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
  paperHealthTool,
  researchDeepDiveTool,
] as const;

/** 名字 → tool 索引，给 framework 路由用。 */
export const toolMap = Object.fromEntries(allTools.map((t) => [t.id, t]));
