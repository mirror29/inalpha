/**
 * ``analyst-quorum-check`` —— Stop hook handler。
 *
 * 用途（ADR-0010 §Stop hook 补丁场景 2）：orchestrator 调了 ``research.deep_dive``
 * 后，如果返回的 ResearchPlan 里 analyst briefs 数量 < quorum 阈值，强拽 LLM 再
 * turn 一次让它说明 / 重跑（或显式告诉用户研究不充分）。
 *
 * 现状（D-8c）：services/research 内部已经用 ``asyncio.gather(return_exceptions=True)``
 * 单 analyst 失败转 placeholder brief（confidence=0.0）。所以"少 analyst"的形态
 * 是 briefs 全在但若干 confidence=0。本 hook 在 orchestrator 侧第二层兜底：
 *
 * - 如果会话内**最近一次** research.deep_dive 的 briefs 里 confidence > 0 的分析师
 *   < ``minQuorum``（默认 3 / 5）→ 强 continue 让 LLM 说明数据不足
 * - 否则正常结束
 *
 * 用注入式 fetcher：测试给固定 brief 列表；生产路径接 sessionState / lastResearchPlan。
 */
import type { HookHandler } from "../types.js";

export type AnalystBriefLite = {
  analyst: string;
  confidence: number;
};

export type LastResearchFetcher = (
  sessionId: string | undefined,
) => Promise<{ briefs: AnalystBriefLite[] } | null>;

export type AnalystQuorumCheckOptions = {
  /** 拉最近 ResearchPlan 的 fetcher；不传 → handler 静默 noop */
  fetcher?: LastResearchFetcher;
  /** confidence > 0 的 analyst 至少要有几个；默认 3 */
  minQuorum?: number;
  /** 命中时返回的 reason 文本模板（``{found}`` / ``{quorum}`` 占位） */
  reasonTemplate?: string;
};

const DEFAULT_REASON_TEMPLATE =
  "the last research.deep_dive returned only {found} usable analyst briefs " +
  "(< quorum {quorum}). before concluding, either re-run research with a longer " +
  "lookbackDays / different timeframe, or explicitly tell the user the research " +
  "is thin and your recommendation has reduced confidence.";

export function createAnalystQuorumCheckHandler(
  opts: AnalystQuorumCheckOptions = {},
): HookHandler {
  const fetcher = opts.fetcher;
  const minQuorum = opts.minQuorum ?? 3;
  const template = opts.reasonTemplate ?? DEFAULT_REASON_TEMPLATE;

  return async (ctx) => {
    if (!fetcher) return {};
    let plan: { briefs: AnalystBriefLite[] } | null;
    try {
      plan = await fetcher(ctx.sessionId);
    } catch {
      return {};
    }
    // 没研究过 / 研究上下文已清 → 不阻拦（用户可能只是查行情）
    if (!plan) return {};
    const usable = plan.briefs.filter((b) => b.confidence > 0);
    if (usable.length >= minQuorum) return {};
    return {
      continue: false,
      reason: template
        .replace("{found}", String(usable.length))
        .replace("{quorum}", String(minQuorum)),
    };
  };
}
