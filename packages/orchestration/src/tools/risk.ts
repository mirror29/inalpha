/**
 * 风控自检 / 解锁的 Mastra tool（ADR-0006 Step 5）。
 *
 * 3 个 tool：
 *
 * - `risk.describe_rules` —— 列出启动时加载的 RiskRule 配置（agent 自检/解释用）
 * - `risk.list_locks`     —— 列当前 active locks（agent 决策前看看是否被锁）
 * - `risk.unlock`         —— 人工解锁（**`modelInvocable: false`**，仅 UI 触发，ADR-0011）
 *
 * `risk.unlock` 走 [ADR-0018 askUserChoice](../../../../docs/miro/decisions/0018-ask-user-question-as-tool.md)
 * 的等价 UI 人工确认。LLM 不应直接调，但 client 层一致暴露，权限隔离在 tool 层。
 *
 * **未接入 wired-tools / index.ts** —— 用户 review 后手动接入（避免与 D-9 并行
 * 工作的 untracked 改动冲突）。
 */
import { createTool } from "@mastra/core/tools";
import { z } from "zod";

import { mintServiceToken } from "../auth.js";
import { RiskClient } from "../clients/risk.js";
import { getSettings } from "../config.js";

type ToolRequestContext = { authToken?: string };

async function getClient(ctx?: ToolRequestContext): Promise<RiskClient> {
  const settings = getSettings();
  const token = ctx?.authToken ?? (await mintServiceToken({ sub: "service:orchestration" }));
  return new RiskClient({ baseUrl: settings.paperServiceUrl, token });
}

// ────────────────────────────────────────────────────────────────────
// risk.describe_rules
// ────────────────────────────────────────────────────────────────────

export const riskDescribeRulesTool = createTool({
  id: "risk.describe_rules",
  description: `
    列出当前 RiskEngine 加载的 RiskRule 配置（含 short_desc 描述）。

    何时用：
    - 用户问"现在有哪些风控规则"/"为什么我的订单被拒"
    - 准备下单前自检"哪些 rule 可能拦我"
    - 给用户报告系统当前风控强度

    何时不用：
    - 想知道"现在被锁了什么" → 用 risk.list_locks（这是 active 状态，不是配置）
    - 修改规则配置 → 不行，必须改 services/paper/configs/risk_rules.toml 并重启 service

    坑：返回的是**启动时加载**的配置，运行时改了 toml 也不会反映在这里。
  `.trim(),
  inputSchema: z.object({}),
  execute: async (_input, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listRules();
  },
});

// ────────────────────────────────────────────────────────────────────
// risk.list_locks
// ────────────────────────────────────────────────────────────────────

export const riskListLocksTool = createTool({
  id: "risk.list_locks",
  description: `
    列当前 active 的风控锁（RiskRule 命中后写入 risk_locks 表的行）。

    何时用：
    - 用户问"现在哪些 symbol/市场 被锁了"
    - 订单被拒后看锁定到何时
    - 调试为什么 strategy 不能下单

    何时不用：
    - 想看 rule 配置 → 用 risk.describe_rules
    - 想解锁 → 用 risk.unlock（且必须人工 UI 触发）

    过滤参数：scope='global'/'market'/'symbol'；market='binance'/'nasdaq'...；symbol='BTC/USDT@binance'。

    坑：当前 risk_locks 表实际由 reconcile worker 同步（独立任务未完成），
    InMemoryLockStore 的运行时状态可能与表内容不一致。
  `.trim(),
  inputSchema: z.object({
    scope: z.enum(["global", "market", "symbol"]).optional(),
    market: z.string().optional(),
    symbol: z.string().optional(),
    limit: z.number().int().positive().max(500).optional(),
  }),
  execute: async (input, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.listLocks(input ?? {});
  },
});

// ────────────────────────────────────────────────────────────────────
// risk.unlock —— modelInvocable: false（人工 UI 触发）
// ────────────────────────────────────────────────────────────────────

/**
 * 人工解锁。**绝对不让 LLM 调**（ADR-0011 modelInvocable）。
 *
 * 暴露为 Mastra tool 仅为让 UI / 后台脚本可走统一 tool 调用通路。Mastra 注册时
 * 必须显式标记 `modelInvocable: false` 或者放在 admin-only tool registry。
 */
export const riskUnlockTool = createTool({
  id: "risk.unlock",
  description: `
    **人工** 解除指定 risk_lock。⚠️ 不应该被 LLM 调（modelInvocable: false）。

    何时用：
    - UI 后台 / 管理员 CLI 手动操作
    - 紧急情况绕过风控（必须写明 reason 进审计）

    何时不用：
    - LLM 决策需要解锁 → **不行**，必须人工拍板

    坑：active=FALSE 软删，行留下作审计。再次 unlock 同 id 返 ok=false。
  `.trim(),
  inputSchema: z.object({
    lock_id: z.number().int().positive(),
    reason: z.string().min(1).max(500),
  }),
  execute: async (input, ctx) => {
    const tc = ctx?.requestContext as ToolRequestContext | undefined;
    const client = await getClient(tc);
    return await client.unlock(input.lock_id, input.reason);
  },
});

// ────────────────────────────────────────────────────────────────────
// 一键导出（接入 index.ts 时引用）
// ────────────────────────────────────────────────────────────────────

/** 数组形态——给 ``tools/index.ts`` 的 spread / forEach / wireToolList 用。 */
export const riskRuleTools = [
  riskDescribeRulesTool,
  riskListLocksTool,
  riskUnlockTool,
] as const;
