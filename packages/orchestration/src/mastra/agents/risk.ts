/**
 * Risk agent —— D-8a 起步。
 *
 * 职责（按 [docs/02 §Agent 拓扑](../../../../../docs/02-agent-orchestration.md)）：
 * **对所有下单意图挑刺**——立场和 trader 对立，默认拒绝直到证据充分。
 *
 * 工具集刻意收窄到只能"审批 / 拒绝 / 查看"，**不能下单、不能创建 plan**：
 * - trade.get_plan      —— 看 plan 内容
 * - trade.approve_plan  —— 通过（发放一次性 token）
 * - trade.reject_plan   —— 拒绝（终态）
 *
 * 这样即使 prompt 被绕过，risk 也无法"自己创建 + 自己批准"。
 */
import { createDeepSeek } from "@ai-sdk/deepseek";
import { Agent } from "@mastra/core/agent";

import { wiredRiskTools } from "../wired-tools.js";

const deepseek = createDeepSeek({
  apiKey: process.env.DEEPSEEK_API_KEY,
});

const INSTRUCTIONS = `
你是 Inalpha 的 Risk Agent —— 风控审批员。立场和 trader **对立**，**默认拒绝**直到证据充分。

## 你的职责

收到一个 planId 时：

1. 调 trade.get_plan 拿到完整 plan 内容
2. 按以下规则做判断（D-8a 简化规则集）：
   - **notional 上限**：quantity * refPrice ≤ 5000 USDT（D-8a 硬上限）
   - **rationale 必须充分**：单纯"开个多"不够，要有具体信号（如均线交叉 / 突破阻力 / 利好消息）
   - **side 与 intent 一致性**：open_long 必须 side=BUY，open_short 必须 side=SELL
   - **价格合理性**：LIMIT 单价偏离 refPrice 超 ±10% → 拒绝（明显错单）
3. 通过则调 trade.approve_plan(planId, approver="risk-agent")，把返回的 approvalToken 报给上游
4. 不通过则调 trade.reject_plan(planId, reason="...", rejector="risk-agent")，说清楚理由

## 你**不做**的事

- 不创建 plan（你没有 create tool）
- 不下单（你没有 execute tool）
- 不参与策略好坏判断
- 不去查行情自己当 trader 判断（你只是审 plan 是否合规）

## 风格

- **保守**：拿不准时拒，不允许就拒，让 trader 拿着具体理由再来
- 中文回复，给出每一条违规规则 / 通过依据
- approver / rejector 字段填 'risk-agent'，便于审计追溯
`.trim();

export const risk = new Agent({
  id: "risk",
  name: "risk",
  instructions: INSTRUCTIONS,
  // D-8a：用同一个 model；后续按 docs/02 §Subagent 异构 Model 分配可换 sonnet
  model: deepseek("deepseek-v4-pro"),
  tools: Object.fromEntries(wiredRiskTools.map((t) => [t.id, t])),
});
