/**
 * D-8a 默认 Permission 规则。
 *
 * 对应 ADR-0011 §规则文件示例 + ADR-0012 plan-exec 强制路径。
 *
 * 设计要点：
 *
 * - **直接下单路径全部 deny**（``paper.submit_order*`` / ``live.submit_order*``）
 *   ——LLM 唯一可达路径是 ``trade.create_plan`` → ``trade.approve_plan`` → ``trade.execute_plan``
 * - 只读 / 查询 tool 全部 allow
 * - 回测 + 策略 lifecycle allow
 * - plan-exec 三件套全部 allow（approval_token 自身就是凭证；不再叠加 permission ask）
 *
 * defaultMode = ``ask``：保守 fail-closed（D-8b' review B7 切换）。旧 ``allow``
 * 默认 + predicate 缺字段返 false 会让 ``live.submit_order(notional<1000)`` 缺
 * notional 时绕过 ask 走 allow，大单意外通过。fail-closed 后未列名 tool 必须
 * 显式声明。
 */
import type { PermissionConfig } from "./types.js";

export const DEFAULT_PERMISSIONS: PermissionConfig = {
  defaultMode: "ask",

  allow: [
    // 只读 / 信息查询
    "data.*",
    "web.*",
    "paper.list_*",
    "paper.get_*",
    "paper.run_backtest",
    "paper.compose_strategy",
    "paper.author_strategy",
    "paper.health",
    "research.deep_dive",
    "research.*",
    "factor.*",
    "scheduler.list_*",
    "scheduler.get_*",
    "scheduler.create_job",
    "scheduler.set_enabled",
    "scheduler.trigger_job",

    // Swarm 批量回测（ADR-0025）：只读，无下单路径
    "swarm.*",

    // Skill 方法论按需读取（ADR-0046）：只读包内本地文本，风险低于 web.*
    "skill.*",

    // MCP 只读公开源（ADR-0009）：coingecko 是零密钥公开加密行情，风险类同 data.*/web.*。
    // 其余 mcp__* 不在此列 → 由 defaultMode:ask fail-closed 兜底（第三方 tool 默认审批）。
    "mcp__coingecko__*",

    // 策略 lifecycle（D-8b 起会有）
    "paper.start_strategy",
    "paper.stop_strategy",

    // Plan/Exec 工具（ADR-0012）
    // 把 plan 三件套全部 allow ——LLM 想交易必须走完整链路；approval_token 是真正的护栏
    "trade.create_plan",
    "trade.approve_plan",
    "trade.execute_plan",
    "trade.reject_plan",
    "trade.get_plan",

    // D-9 spike：沙盒（ADR-0020 第二道运行隔离）
    // 60s 内的运行允许（spike + 临时计算）；更长走 ask（人工审批）。
    // 第一道 AST 审计 + 第三道协议契约校验留给 Phase B 接入。
    "sandbox.run_code(timeoutMs<=60000)",

    // 玄学彩蛋（六爻 / 塔罗）—— 纯娱乐、本地确定性、不碰钱不碰数据，allow
    "divination.*",
  ],

  ask: [
    // D-8a 暂无 live 引擎；规则提前写好作 forward-compat
    "live.submit_order(notional<1000)",
    "risk.update_config",

    // D-9 · 候选 → 正式策略（ADR-0018 / D-9.1b：askUserChoice 接通后改回 ask）
    // 后端硬校验仍在（fitness IS NOT NULL + status='candidate'）作为第二道防线
    "paper.promote_candidate",
  ],

  deny: [
    // 旧的"直接下单"路径全部禁——强制走 ADR-0012 plan/exec
    "paper.submit_order",
    "paper.submit_order_intent",

    // live 大额 / 全局动作
    "live.submit_order(notional>=10000)",
    "live.close_all_positions",
    "live.cancel_all_orders",
    "live.emergency_stop_all",

    // 不可逆破坏操作
    "strategy.delete_history",
    "risk.disable_all_checks",
    "secret.rotate_api_key",
    "system.shutdown",
  ],
};
