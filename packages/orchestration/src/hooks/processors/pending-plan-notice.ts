/**
 * ``pending-plan-notice`` —— chat 路径的 pending plan 残留警示 processor（issue #65）。
 *
 * 背景：ADR-0010 §Stop hook 承诺"turn 结束前检查未执行 plan"。scheduler 路径
 * 由我们自己调 ``agent.generate``，能用 ``StopHookRunner`` 真正强制再 turn；
 * 但 chat 路径走 Mastra server runtime——**Mastra 1.36 没有"turn 结束后强制
 * 续 loop"的钩子位**（``processOutputStep`` 只能改消息不能续步）。本 processor
 * 是 chat 路径的务实替代：turn 收尾时若有未执行 plan，把警示**追加到最终
 * 回复文本**，用户与下一 turn 的 LLM（消息已入 memory）都能看见残留。
 *
 * 何时触发：本 turn 调过 ``trade.create_plan`` / ``trade.approve_plan``（避免
 * 每条闲聊都打 paper HTTP）且 fetcher 查到 pending_approval / approved 残留。
 *
 * 失败语义：fetcher 抛错静默放过（护栏失败不应拖垮用户体验，与
 * ``createPendingPlanCheckHandler`` 的 fail-safe 一致）。
 */
import type { OutputProcessor } from "@mastra/core/processors";

import type { PendingPlanFetcher } from "../handlers/pending-plan-check.js";
import { AUTH_SUB_KEY } from "../with-hooks.js";

/** 命中这些 tool 才查残留（plan 状态只会被这两个动作推进到"未执行"态）。 */
const PLAN_MUTATING_TOOLS = ["trade.create_plan", "trade.approve_plan"];

/**
 * 默认模板是**语言中立的机器风格状态行**（PR review · CLAUDE.md §3）：
 * 用户可见文本不能写死任何自然语言（英文散文对中文/日文用户 = 中英混排 bug）。
 * 这里只保留 key-value 结构 + tool 名 / plan id（专有名词本就不翻译），
 * 下一 turn LLM 在 memory 里读到后会按用户语言转述与执行。
 * 需要完整自然语言版本时通过 ``noticeTemplate`` 按 locale 注入。
 */
const DEFAULT_NOTICE_TEMPLATE =
  "[system_notice] pending_plans: {count} ({ids}) → trade.execute_plan / trade.reject_plan " +
  "(approvalToken: trade.approve_plan → token; approved: trade.get_plan)";

export type PendingPlanNoticeOptions = {
  /** 拉 plan 的函数；不传 → processor 静默 noop（dev / 测试时友好） */
  fetcher?: PendingPlanFetcher;
  /** 警示文本模板（含 ``{count}`` / ``{ids}`` 占位） */
  noticeTemplate?: string;
};

function toolNameOf(call: unknown): string {
  const c = call as { toolName?: unknown; payload?: { toolName?: unknown } };
  const name = c?.toolName ?? c?.payload?.toolName;
  return typeof name === "string" ? name : "";
}

/**
 * 创建 pending-plan-notice processor，挂到 Agent ``outputProcessors``。
 *
 * 用法：``new Agent({ ..., outputProcessors: [createPendingPlanNoticeProcessor({ fetcher })] })``
 */
export function createPendingPlanNoticeProcessor(
  opts: PendingPlanNoticeOptions = {},
): OutputProcessor {
  const fetcher = opts.fetcher;
  const template = opts.noticeTemplate ?? DEFAULT_NOTICE_TEMPLATE;

  return {
    id: "pending-plan-notice",
    async processOutputResult({ messages, result, requestContext }) {
      if (!fetcher) return messages;
      const calls = (result?.steps ?? []).flatMap((step) => step.toolCalls ?? []);
      const names = calls.map(toolNameOf);
      // 契约漂移留痕（PR review）：本判定依赖 Mastra 内部 steps[].toolCalls[].toolName，
      // 非公共稳定 schema。一旦结构变化，nullish guard 让这里静默 noop——护栏失效
      // 且无人察觉。"有 toolCalls 条目但一个 toolName 都解析不出" = 结构已变的信号。
      // warn 而非 debug（PR review）：生产默认不出 debug，漂移信号等于没留；
      // 只在结构真变了才触发，不构成常态噪音
      if (calls.length > 0 && names.every((n) => n === "")) {
        console.warn(
          "[pending-plan-notice] steps[].toolCalls 无可解析 toolName——Mastra 结构可能已变更，pending plan 警示在静默失效",
        );
      }
      const touchedPlans = names.some((n) => PLAN_MUTATING_TOOLS.includes(n));
      if (!touchedPlans) return messages;

      // 多租户:把中间件注入的已认证 sub(AUTH_SUB_KEY)传给 fetcher,使 /plans 只查登录
      // 用户账户——否则恒查 console:dev,对真实登录用户既漏查其 plan,又会把 console
      // 账户的 plan_id 泄进回复(跨租户信息泄露)。
      const authSub =
        typeof requestContext?.get === "function"
          ? requestContext.get(AUTH_SUB_KEY)
          : undefined;
      let plans;
      try {
        plans = await fetcher(typeof authSub === "string" ? authSub : undefined);
      } catch {
        return messages; // 护栏失败不阻断回复
      }
      if (plans.length === 0) return messages;

      const lastAssistant = [...messages]
        .reverse()
        .find((m) => m.role === "assistant");
      if (lastAssistant === undefined) return messages;
      // 防御（PR review）：Mastra 部分路径允许 string content；push 崩了会拖垮整条回复
      if (!Array.isArray(lastAssistant.content?.parts)) return messages;

      const ids = plans.map((p) => p.plan_id).join(", ");
      const notice = template
        .replace("{count}", String(plans.length))
        .replace("{ids}", ids);
      lastAssistant.content.parts.push({ type: "text", text: `\n\n${notice}` });
      return messages;
    },
  };
}
