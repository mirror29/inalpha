/**
 * Orchestrator agent —— Inalpha 总调度（supervisor pattern）。
 *
 * D-7：一个 agent 挂全部 tool 直接跑（已被本次升级取代）。
 * D-8a：supervisor pattern —— orchestrator 自己不下场做事，路由给 trader / risk。
 *   - trader：下单 / 查行情 / 跑回测
 *   - risk：审批 plan
 *   - orchestrator 保留 paper.health 等"路由层级"工具
 *
 * 关键约束：
 *
 * - orchestrator **不能直接** create / execute plan（没挂这些 tool）
 * - 下单流必须 trader.create_plan → risk.approve_plan → trader.execute_plan
 * - 这是 [ADR-0012](../../../../../docs/decisions/0012-plan-exec-separation.md) 的
 *   "LLM 无直接下单路径"约束的工程实现：通过 tool 集隔离而非靠 prompt 自律。
 */
import { createDeepSeek } from "@ai-sdk/deepseek";
import { Agent } from "@mastra/core/agent";

import { wireToolList } from "../wired-tools.js";
import {
  dataBackfillBarsTool,
  dataGetBarsTool,
  paperHealthTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
} from "../../tools/index.js";
import { risk } from "./risk.js";
import { trader } from "./trader.js";

// 把 orchestrator 自己用的 tool 套上 hooks + permissions（与 trader / risk 共享同
// 一个 runner / engine 实例——见 wired-tools.ts 单例约定）。
const orchestratorTools = wireToolList([
  dataGetBarsTool,
  dataBackfillBarsTool,
  paperListStrategiesTool,
  paperRunBacktestTool,
  paperHealthTool,
]);

const deepseek = createDeepSeek({
  apiKey: process.env.DEEPSEEK_API_KEY,
});

const INSTRUCTIONS = `
你是 Inalpha 总调度（orchestrator）—— 量化交易助手的对话主入口。**你不下场做事，只路由**。

## 你能直接做的事（tool）

- data.get_bars / data.backfill_bars —— 行情数据
- paper.list_strategies / paper.run_backtest —— 回测查询
- paper.health —— 健康检查

## 你必须委派的事（subagent）

- **任何下单 / 平仓 / 调仓**：委派给 trader
- **任何风控审批**：委派给 risk

**关键：你没有 trade.create_plan / approve_plan / execute_plan 这些 tool**——
LLM 想下单只能调 trader。这是工程硬约束，不是建议。

## 完整下单流程

用户："帮我开 0.001 BTC 多单"
1. 调 trader：让它创建 plan（trader 会先取 refPrice 再 create_plan）
2. 拿到 planId 后调 risk：让它审批
3. 审批通过拿到 approvalToken
4. 把 planId + approvalToken 交给 trader 让它 execute_plan
5. 把 order 结果汇总给用户

## 时间默认值约定

data.* / paper.run_backtest 的 fromTs / toTs 都是 optional，省略时默认"近 1 年"。
用户没明确给时间段时**不要主动追问**，直接走默认，连参数都不用传。
只有用户明确说"用上个月" / "2024 Q3" 这种才需要算具体 ISO 时间填进去。

## 重要约束

- symbol 必须是 CCXT 格式：BTC/USDT
- timeframe 只支持：1m / 5m / 15m / 1h / 4h / 1d
- venue 只支持 binance
- 3 个内置策略：sma_cross / buy_and_hold / mean_reversion（不确定先 paper.list_strategies）
- 看到 NO_BARS_AVAILABLE 错误：先 data.backfill_bars 再重试

## 风格

- 中文回复，简洁
- 报告数字精确到 2 位小数
- 不确定的话先反问，不要瞎猜参数
- **能委派就委派**，不要为了省一步自己 polyfill
`.trim();

export const orchestrator = new Agent({
  id: "orchestrator",
  name: "orchestrator",
  instructions: INSTRUCTIONS,
  model: deepseek("deepseek-v4-pro"),
  // supervisor pattern：subagent 自动暴露成 tool 给 LLM 调
  agents: { trader, risk },
  // 保留路由层级 tool（不含 trade-plan 系列，强制走 trader）
  tools: Object.fromEntries(orchestratorTools.map((t) => [t.id, t])),
});
