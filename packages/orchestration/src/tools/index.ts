/**
 * Tool 注册聚合。
 *
 * - D-7 起步：data + paper 5 个
 * - D-8a：trade-plan 5 个（create / approve / reject / execute / get）
 * - D-8b：research 1 个（deep_dive）
 * - D-9 spike：sandbox 1 个（run_code），ADR-0020 第二道运行隔离
 * - D-10：web 2 个（search / search_news）+ fundamentals 1 个（get_fundamentals）
 * - D-12+：market 4 个（市场级行情归因：news / sectors / moneyflow / movers）
 */
import {
  dataBackfillBarsTool,
  dataGetBarsTool,
  dataGetFundamentalsTool,
  dataGetTickerTool,
  dataSearchSymbolTool,
  dataTools,
} from "./data.js";
import {
  paperAssessWindowConsistencyTool,
  paperCheckSensitivityTool,
  paperComposeStrategyTool,
  paperCvBacktestTool,
  paperDepositCashTool,
  paperGetAccountTool,
  paperHealthTool,
  paperListArchetypesTool,
  paperListBacktestRunsTool,
  paperListBacktestTradesTool,
  paperListCashFlowsTool,
  paperListOrdersTool,
  paperListPositionsTool,
  paperListStrategiesTool,
  paperListStrategyRunDecisionsTool,
  paperListStrategyRunsTool,
  paperResetAccountTool,
  paperRunBacktestTool,
  paperStartStrategyTool,
  paperStopStrategyTool,
  paperTools,
} from "./paper.js";
import {
  factorCatalogTool,
  factorEvaluateCandidateTool,
  factorListCandidatesTool,
  factorPanelScoreTool,
  factorProposeTool,
  factorRunDiscoveryTool,
  factorScoreTool,
  factorTimingTool,
  factorTools,
} from "./factor.js";
import {
  divinationCastHexagramTool,
  divinationDrawTarotTool,
  divinationTools,
} from "./divination.js";
import { researchDeepDiveTool, researchTools } from "./research.js";
import { researchParallelDiveTool } from "./research-parallel.js";
import {
  riskDescribeRulesTool,
  riskListLocksTool,
  riskRuleTools,
  riskUnlockTool,
} from "./risk.js";
import { sandboxRunCodeTool, sandboxTools } from "./sandbox.js";
import {
  schedulerCreateJobTool,
  schedulerGetJobTool,
  schedulerListJobsTool,
  schedulerListRunsTool,
  schedulerSetEnabledTool,
  schedulerTools,
  schedulerTriggerJobTool,
} from "./scheduler.js";
import {
  paperAuthorStrategyTool,
  paperAuthoringTools,
  paperGetCandidateTool,
  paperListCandidatesTool,
  paperPromoteCandidateTool,
} from "./strategy.js";
import {
  dataGetMarketMoneyflowTool,
  dataGetMarketMoversTool,
  dataGetMarketNewsTool,
  dataGetMarketSectorsTool,
  marketTools,
} from "./market.js";
import { skillReadTool, skillTools } from "./skill.js";
import { swarmRunBacktestGridTool, swarmTools } from "./swarm.js";
import { webFetchTool, webSearchNewsTool, webSearchTool, webTools } from "./web.js";
import {
  approveTradePlanTool,
  createTradePlanTool,
  executeTradePlanTool,
  getTradePlanTool,
  rejectTradePlanTool,
  tradePlanTools,
} from "./trade-plan.js";
import {
  evolverGetEvolutionTool,
  evolverRunEvolutionTool,
  evolverTools,
} from "./evolver.js";

export {
  approveTradePlanTool,
  createTradePlanTool,
  dataBackfillBarsTool,
  dataGetBarsTool,
  dataGetFundamentalsTool,
  dataGetMarketMoneyflowTool,
  dataGetMarketMoversTool,
  dataGetMarketNewsTool,
  dataGetMarketSectorsTool,
  dataGetTickerTool,
  dataSearchSymbolTool,
  divinationCastHexagramTool,
  divinationDrawTarotTool,
  executeTradePlanTool,
  factorCatalogTool,
  factorEvaluateCandidateTool,
  factorListCandidatesTool,
  factorPanelScoreTool,
  factorProposeTool,
  factorRunDiscoveryTool,
  factorScoreTool,
  factorTimingTool,
  getTradePlanTool,
  paperAuthorStrategyTool,
  paperAssessWindowConsistencyTool,
  paperCheckSensitivityTool,
  paperComposeStrategyTool,
  paperCvBacktestTool,
  paperDepositCashTool,
  paperGetAccountTool,
  paperGetCandidateTool,
  paperHealthTool,
  paperListArchetypesTool,
  paperListBacktestRunsTool,
  paperListBacktestTradesTool,
  paperListCandidatesTool,
  paperListCashFlowsTool,
  paperListOrdersTool,
  paperListPositionsTool,
  paperListStrategiesTool,
  paperListStrategyRunDecisionsTool,
  paperListStrategyRunsTool,
  paperPromoteCandidateTool,
  paperResetAccountTool,
  paperRunBacktestTool,
  paperStartStrategyTool,
  paperStopStrategyTool,
  rejectTradePlanTool,
  researchDeepDiveTool,
  researchParallelDiveTool,
  riskDescribeRulesTool,
  riskListLocksTool,
  riskRuleTools,
  riskUnlockTool,
  sandboxRunCodeTool,
  schedulerCreateJobTool,
  schedulerGetJobTool,
  schedulerListJobsTool,
  schedulerListRunsTool,
  schedulerSetEnabledTool,
  schedulerTriggerJobTool,
  skillReadTool,
  swarmRunBacktestGridTool,
  webFetchTool,
  webSearchNewsTool,
  webSearchTool,
};

/** 所有 tool 数组，给 Mastra Agent 直接挂载。 */
export const allTools = [
  ...dataTools,
  ...paperTools,
  ...paperAuthoringTools,
  ...tradePlanTools,
  ...researchTools,
  researchParallelDiveTool,
  // 接现成因子库 + 有效性择时（docs/miro/11）
  ...factorTools,
  ...swarmTools,
  ...schedulerTools,
  ...sandboxTools,
  // D-10：web 搜索
  ...webTools,
  // D-12+：市场级行情归因（快讯/板块/资金/强势股，无需 symbol）
  ...marketTools,
  // ADR-0046：投研方法论 skill 按需读取（progressive disclosure）
  ...skillTools,
  // ADR-0006 §D6：risk.* agent 自检 + 解锁（unlock 在 permissions 层禁 LLM 直调）
  riskDescribeRulesTool,
  riskListLocksTool,
  riskUnlockTool,
  // 玄学彩蛋（六爻 / 塔罗）—— 纯娱乐，硬隔离于决策
  ...divinationTools,
  // E2 演化引擎（LLM 驱动策略变异 + 沙盒 + 评估）
  ...evolverTools,
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
  dataGetFundamentalsTool,
  // 公司名 → ticker 解析（候选池构建，禁训练记忆猜代码）
  dataSearchSymbolTool,
  // D-10：web 搜索；web.fetch 读原文补证据链最后一公里
  webSearchTool,
  webSearchNewsTool,
  webFetchTool,
  // D-12+：市场级行情归因（快讯/板块/资金/强势股，无需 symbol，venue 按 market 路由）
  dataGetMarketNewsTool,
  dataGetMarketSectorsTool,
  dataGetMarketMoneyflowTool,
  dataGetMarketMoversTool,
  // ADR-0046：投研方法论 skill 按需读取（清单在 <skills> prompt 段）
  skillReadTool,
  paperListStrategiesTool,
  // ADR-0051：策略原型库——写策略前按因子 kind 取骨架当起点
  paperListArchetypesTool,
  paperAssessWindowConsistencyTool,
  paperRunBacktestTool,
  // D-12 · 参数邻域敏感性（promote 前必跑，cliff = 过拟合信号）
  paperCheckSensitivityTool,
  // ADR-0028 · 多路径时序 CV（深度/稳健评估，看 CPCV 中位 Sharpe + DSR）
  paperCvBacktestTool,
  paperHealthTool,
  // 研究
  researchDeepDiveTool,
  // D-13 · 并行多视角研究（bull/bear/technical/macro 独立扇出）
  researchParallelDiveTool,
  // 接现成因子库（docs/miro/11）：有效因子择时 + 横截面选股 + 目录 + 深挖打分
  factorTimingTool,
  factorScoreTool,
  factorPanelScoreTool,
  factorCatalogTool,
  // D-12 · 因子发现 L1：自定义表达式因子评估（白名单 DSL）+ 候选池
  // （register 门只在 dashboard——agent 物理上没有转正工具）
  factorEvaluateCandidateTool,
  factorProposeTool,
  factorListCandidatesTool,
  factorRunDiscoveryTool,
  // D-8c 研究→策略 链路（compose 路由 + 历史回测查询）
  paperComposeStrategyTool,
  paperListBacktestRunsTool,
  // D-12 · 逐笔成交明细（迭代诊断"亏在哪几笔"）
  paperListBacktestTradesTool,
  // D-9 · ADR-0020 E1 MVP：LLM 自创策略候选（compose 不够用时走这条）
  paperAuthorStrategyTool,
  paperListCandidatesTool,
  paperGetCandidateTool,
  // D-9 · 候选 → 正式（permission 默认 ask，弹气泡二次确认）
  paperPromoteCandidateTool,
  // D-11 · live runner（issue #1）：promoted 候选按行情自动跑 + 决策复盘
  paperStartStrategyTool,
  paperStopStrategyTool,
  paperListStrategyRunsTool,
  paperListStrategyRunDecisionsTool,
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
  // 账户外生资金事件:充值/重置(permission ask 弹气泡)+ 流水查询
  paperDepositCashTool,
  paperResetAccountTool,
  paperListCashFlowsTool,
  // D-9 类 Hermes 定时管理（让 agent 在对话里跑 scheduler）
  schedulerCreateJobTool,
  schedulerListJobsTool,
  schedulerGetJobTool,
  schedulerSetEnabledTool,
  schedulerTriggerJobTool,
  schedulerListRunsTool,
  // D-9 spike：沙盒（ADR-0020 第二道运行隔离）
  sandboxRunCodeTool,
  // D-9.1b · ADR-0006 §D6：agent 自检 + 解释风控（unlock 不挂，admin UI 用 allTools 走）
  riskDescribeRulesTool,
  riskListLocksTool,
  // 玄学彩蛋（六爻 / 塔罗）—— 仅用户明确点名时召唤，输出禁入决策
  divinationCastHexagramTool,
  divinationDrawTarotTool,
  // E2 演化引擎（LLM 驱动策略变异 + 沙盒 + 评估）
  evolverRunEvolutionTool,
  evolverGetEvolutionTool,
] as const;

/** 名字 → tool 索引，给 framework 路由用。 */
export const toolMap = Object.fromEntries(allTools.map((t) => [t.id, t]));
