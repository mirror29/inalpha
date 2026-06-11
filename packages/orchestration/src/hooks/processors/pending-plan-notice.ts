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

/** 命中这些 tool 才查残留（plan 状态只会被这两个动作推进到"未执行"态）。 */
const PLAN_MUTATING_TOOLS = ["trade.create_plan", "trade.approve_plan"];

const DEFAULT_NOTICE_TEMPLATE =
  "[system_notice] {count} trade plan(s) ({ids}) are still in " +
  "approved / pending_approval state — not executed yet. " +
  "Execute them (trade.execute_plan) or reject (trade.reject_plan) explicitly.";

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
    async processOutputResult({ messages, result }) {
      if (!fetcher) return messages;
      const touchedPlans = (result?.steps ?? []).some((step) =>
        (step.toolCalls ?? []).some((call) =>
          PLAN_MUTATING_TOOLS.includes(toolNameOf(call)),
        ),
      );
      if (!touchedPlans) return messages;

      let plans;
      try {
        plans = await fetcher(undefined);
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
