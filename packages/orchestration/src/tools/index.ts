/**
 * Tool 注册聚合：D-7 起步 5 个工具。
 */
import { dataBackfillBarsTool, dataGetBarsTool, dataTools } from "./data.js";
import { paperHealthTool, paperListStrategiesTool, paperRunBacktestTool, paperTools } from "./paper.js";

export {
  dataBackfillBarsTool,
  dataGetBarsTool,
  paperHealthTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
};

/** 所有 tool 数组，给 Mastra Agent 直接挂载。 */
export const allTools = [...dataTools, ...paperTools] as const;

/** 名字 → tool 索引，给 framework 路由用。 */
export const toolMap = Object.fromEntries(allTools.map((t) => [t.id, t]));
