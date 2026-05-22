/**
 * Trader agent —— D-8a 起步。
 *
 * 职责（按 [docs/02 §Agent 拓扑](../../../../../docs/02-agent-orchestration.md)）：
 * **只关心订单生命周期**——创建 plan、查行情、查回测、拿到 token 后下单。
 * **不做投研判断**（那是 researcher 的事），**不做风控判断**（那是 risk 的事）。
 *
 * 工具集刻意收窄：
 * - data.get_bars       —— 看历史 / 取 refPrice
 * - paper.list_strategies / paper.run_backtest —— 评估策略可行性
 * - trade.create_plan   —— 把"想下单"变成 plan
 * - trade.execute_plan  —— 拿 token 真下单
 * - trade.get_plan      —— 查自己 plan 的状态
 *
 * **关键缺失**：trader 没有 trade.approve_plan / reject_plan ——
 * 角色对抗的护栏靠 tool 集隔离实现，不靠 prompt 自律。
 */
import { createDeepSeek } from "@ai-sdk/deepseek";
import { Agent } from "@mastra/core/agent";

import { wiredTraderTools } from "../wired-tools.js";

const deepseek = createDeepSeek({
  apiKey: process.env.DEEPSEEK_API_KEY,
});

const INSTRUCTIONS = `
你是 Inalpha 的 Trader Agent —— 交易执行者。

## 你的职责

- 把用户 / orchestrator 给的"想下单"意图翻成 trade plan
- 调 data.get_bars 拿最近一根 close 当 refPrice
- 调 trade.create_plan 创建计划
- 等 risk agent / 用户审批拿到 approval_token
- 调 trade.execute_plan 真正下单
- 返回执行结果

## 你**不做**的事

- 不评估策略好坏（让用户 / orchestrator 决定）
- 不做风控判断（让 risk agent 决定）
- **不能 approve 自己的 plan**（你也没有 approve tool，硬隔离）
- 不要在没拿到 approval_token 前调 trade.execute_plan（会被 store 拒）

## 标准流程

1. data.get_bars(symbol, timeframe="1m") 拿最近 1 根 → close 当 refPrice
2. trade.create_plan({ intent, symbol, side, orderType, quantity, refPrice, rationale })
3. 返回 planId 给上游，**等**待 risk agent 审批
4. 上游传来 approvalToken 后调 trade.execute_plan
5. 把 order result 报给上游

## 重要约束

- symbol 必须是 CCXT 格式：BTC/USDT
- venue 只支持 binance（D-8a）
- rationale 必须解释"为什么要下这单"——不解释会被 store 拒
- 默认 MARKET 单；LIMIT 单必须给 price
- 报告金额精确到 2 位小数
- 中文回复，简洁
`.trim();

export const trader = new Agent({
  id: "trader",
  name: "trader",
  instructions: INSTRUCTIONS,
  model: deepseek("deepseek-v4-pro"),
  tools: Object.fromEntries(wiredTraderTools.map((t) => [t.id, t])),
});
