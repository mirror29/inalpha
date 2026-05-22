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
 * defaultMode = ``allow`` 是 D-8a 开发友好选择（绿场，没有未知 tool 会自动跑进 ask 卡住）。
 * 上 prod 时按 ADR-0011 §关键约定 2 切到 ``ask``（守保守原则）。
 */
import type { PermissionConfig } from "./types.js";

export const DEFAULT_PERMISSIONS: PermissionConfig = {
  defaultMode: "allow",

  allow: [
    // 只读 / 信息查询
    "data.*",
    "paper.list_strategies",
    "paper.run_backtest",
    "paper.health",
    "paper.get_*",
    "research.deep_dive",
    "factor.*",

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
  ],

  ask: [
    // D-8a 暂无 live 引擎；规则提前写好作 forward-compat
    "live.submit_order(notional<1000)",
    "risk.update_config",
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
