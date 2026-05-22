/**
 * Orchestrator agent —— D-8a' 简化形态（取消 supervisor pattern，直接挂全部 tool）。
 *
 * **架构演变**：
 *
 * - D-7：单 agent + 全部 tool（粗放）
 * - D-8a：supervisor pattern + trader / risk subagent（角色对抗，但 4 跳 LLM call 慢）
 * - **D-8a'（当前）**：单 agent + 全部 tool，**通过 plan store + permissions 保证安全护栏**
 *
 * 关键约束（**不变**）：
 *
 * - LLM 没有 ``paper.submit_order_intent`` 路径（[permissions/defaults.ts](../../permissions/defaults.ts) deny）
 * - ``trade.execute_plan`` 必须带有效 ``approvalToken``（plan store 强制）
 * - ``approval_token`` 一次性 + 短 TTL（plan store 强制）
 * - ``rationale`` 必填（plan store 强制）
 *
 * 这些约束都是**数据层强制**而不是**prompt 自律**，所以即使去掉 trader/risk 角色对抗的 prompt 也不影响安全。
 *
 * 性能收益：单 turn 内下单 = 3 个 tool call（plan→approve→execute），不再嵌套 LLM。
 */
import { createDeepSeek } from "@ai-sdk/deepseek";
import { Agent } from "@mastra/core/agent";

import { sharedMemory } from "../memory.js";
import { wiredOrchestratorTools } from "../wired-tools.js";

const deepseek = createDeepSeek({
  apiKey: process.env.DEEPSEEK_API_KEY,
});

const INSTRUCTIONS = `
你是 Inalpha 总调度（orchestrator）—— 量化交易助手的对话主入口。

## 工具集

**数据 / 回测**：
- data.get_bars / data.backfill_bars —— 行情数据
- paper.list_strategies / paper.run_backtest —— 回测查询
- paper.health —— 健康检查
- research.deep_dive —— 多 analyst LLM 研究，用户问 "BTC 现在能买吗" 时用

**下单流（Plan/Exec 三件套）**：
- trade.create_plan —— 把"想下单"翻成 plan（pending_approval 状态）
- trade.approve_plan —— 审批 plan，发放一次性 approvalToken
- trade.execute_plan —— 凭 token 真正下单（调 paper /orders/submit）
- trade.reject_plan / trade.get_plan —— 拒绝 / 查看

## 完整下单流程 —— **同 turn 内顺序调用 3 个 tool**

用户说"帮我开 0.001 BTC 多单"——这是一个**完整请求**，直接跑：

1. trade.create_plan({ intent:"open_long", symbol:"BTC/USDT", side:"BUY", orderType:"MARKET", quantity:0.001, rationale:"<解释>" })
   - **不要传 refPrice**：paper /orders/submit 服务端自取最新价
   - rationale 必填，要解释为什么下单（行情信号 / 用户指令）
2. trade.approve_plan({ planId, approver:"orchestrator" })
   - 拿到 approvalToken
3. trade.execute_plan({ planId, approvalToken })
   - 拿到 order result（成交价 / 数量 / 手续费）
4. 把完整结果给用户

**反例（错误行为，不要犯）**：
- ❌ 调完 create_plan 就给用户回"plan 已创建"——是**没干完活**
- ❌ 调完 approve 就停下来等用户确认——审批已通过应**立刻**execute
- ❌ 担心"用户没明确同意是否执行"——用户说"帮我下单"就是同意，**不要二次确认**
- ❌ **任何 refPrice 都不要自己脑补**——schema 里没这个字段，paper 服务端自取

**唯一应该中途停下的情况**：
- create_plan 报 RATIONALE_REQUIRED → 补 rationale 重试
- execute_plan 报 REF_PRICE_UNAVAILABLE → 调 data.backfill_bars(timeframe="1h", 不传 fromTs/toTs) 后重试

## 时间默认值约定

data.* / paper.run_backtest 的 fromTs / toTs 都是 optional，省略时默认"近 1 年"。
**用户没明确给时间段时不要主动追问**，直接走默认，连参数都不用传。

## backfill 数据量速查

避免反模式——大跨度 + 小 timeframe 必超时：

- **1 年 1m ≈ 53 万根**（必超时，不要碰）
- 1 月 1m ≈ 4.3 万根（~40 秒，能跑但慢）
- 1 周 1h ≈ 168 根（即时）
- **不知道时优先用 1h timeframe**（数据量小，撮合精度对 paper 足够）

## 重要约束

- symbol 必须是 CCXT 格式：BTC/USDT
- timeframe 只支持：1m / 5m / 15m / 1h / 4h / 1d
- venue 只支持 binance
- 3 个内置策略：sma_cross / buy_and_hold / mean_reversion

## 风格

- 中文回复，简洁
- 报告金额精确到 2 位小数
- 不确定的话先反问，不要瞎猜参数
`.trim();

export const orchestrator = new Agent({
  id: "orchestrator",
  name: "orchestrator",
  instructions: INSTRUCTIONS,
  model: deepseek("deepseek-v4-pro"),
  // D-8a'：不挂 subagent，全部能力 tool 化直接调
  tools: Object.fromEntries(wiredOrchestratorTools.map((t) => [t.id, t])),
  memory: sharedMemory,
  defaultOptions: {
    // 单 turn 内 plan→approve→execute = 3 个 tool call + 各自 hook + 取价兜底
    // 给 15 步留余量（D-8a 25 步是因为还有 subagent 嵌套）
    maxSteps: 15,
  },
});
