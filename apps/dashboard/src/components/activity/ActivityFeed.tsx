"use client";

import { useState } from "react";

import { useLocale, useNow, useTranslations } from "next-intl";
import { ChevronRight, MessageSquare } from "lucide-react";
import { useSWRConfig } from "swr";

import type { ActivityEvent, ActivityTone } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { approvalExecutionReceipt } from "@/lib/approval-execution";
import { cn } from "@/lib/cn";
import { fmtRelative, fmtTime } from "@/lib/format";
import { KindTag } from "./KindTag";

const toneText: Record<ActivityTone, string> = {
  bull: "text-bull",
  fox: "text-fox-red",
  gold: "text-gold",
  cyan: "text-cyan",
  muted: "text-fg-muted",
};

/**
 * 归一化活动流 —— 每行:时间 + 模块标签 + 标题 + 明细 + 结果。
 * risk / fox 行左侧描一道竖红线,扫一眼就能找到被拦截/失败的事件。
 */
export function ActivityFeed({ events }: { events: ActivityEvent[] }) {
  const locale = useLocale();
  const now = useNow({ updateInterval: 10_000 });
  const tf = useTranslations("footer");

  return (
    <ul className="divide-y divide-border-subtle/60">
      {events.map((e) => {
        // 会话事件可点 → 打开右侧对话栏并切到该会话(与底部日志同款交互)。
        const isConversation = e.kind === "conversation";
        const hasApproval = Boolean(e.approvalRequestId);
        const clickable = isConversation || (Boolean(e.href) && !hasApproval);
        const row = (
          <div
            className={cn(
              "flex items-start gap-3 px-4 py-3 transition-colors",
              clickable && "group-hover:bg-bg-elev/40",
              e.tone === "fox" && "border-l-2 border-fox-red/60",
            )}
          >
            {/* 时间列 */}
            <div className="w-16 shrink-0 pt-0.5 text-right font-mono text-[10px] leading-tight text-fg-muted/70">
              <div className="tnum text-fg-muted">{fmtTime(e.ts, locale)}</div>
              <div className="tnum">{fmtRelative(e.ts, now.getTime(), locale)}</div>
            </div>

            {/* 主体 */}
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <KindTag kind={e.kind} />
                <span className="truncate text-fg">{e.title}</span>
                {e.stats?.map((st, i) => (
                  <span
                    key={i}
                    className={cn(
                      "tnum shrink-0 font-mono text-[11px]",
                      toneText[st.tone ?? "muted"],
                    )}
                  >
                    {st.text}
                  </span>
                ))}
                {e.outcome && (
                  <span
                    className={cn(
                      "font-mono text-[10px] uppercase tracking-wider",
                      toneText[e.tone],
                    )}
                  >
                    {e.outcome}
                  </span>
                )}
              </div>
              {e.detail && (
                <p className="mt-0.5 truncate font-mono text-[11px] text-fg-muted">
                  {e.detail}
                </p>
              )}
            </div>

            {isConversation ? (
              <MessageSquare className="mt-0.5 size-4 shrink-0 text-fg-muted/30 group-hover:text-seal" />
            ) : hasApproval ? (
              <ApprovalActions requestId={e.approvalRequestId!} />
            ) : (
              e.href && (
                <ChevronRight className="mt-0.5 size-4 shrink-0 text-fg-muted/30 group-hover:text-cyan/70" />
              )
            )}
          </div>
        );

        return (
          <li key={e.id}>
            {isConversation ? (
              <button
                type="button"
                title={tf("openConversation")}
                onClick={() =>
                  window.dispatchEvent(
                    new CustomEvent("inalpha:open-chat", {
                      detail: { threadId: e.id.replace(/^conv:/, "") },
                    }),
                  )
                }
                className="group block w-full text-left"
              >
                {row}
              </button>
            ) : e.href && !hasApproval ? (
              <Link href={e.href} className="group block">
                {row}
              </Link>
            ) : (
              row
            )}
          </li>
        );
      })}
    </ul>
  );
}

function ApprovalActions({ requestId }: { requestId: string }) {
  const t = useTranslations("activity.approval");
  const { mutate } = useSWRConfig();
  const [submitting, setSubmitting] = useState<"allow" | "deny" | null>(null);
  const [failed, setFailed] = useState(false);
  const [submittedRunId, setSubmittedRunId] = useState<string | null>(null);

  const respond = async (decision: "allow" | "deny") => {
    setSubmitting(decision);
    setFailed(false);
    try {
      const response = await fetch(`/api/permissions/${encodeURIComponent(requestId)}/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision }),
      });
      if (!response.ok) throw new Error(`approval failed (${response.status})`);
      if (decision === "allow") {
        const receipt = approvalExecutionReceipt(await response.json());
        if (receipt.kind === "failed") throw new Error("approved task submission failed");
        if (receipt.kind === "submitted") setSubmittedRunId(receipt.runId);
      }
      await mutate("/api/activity");
    } catch {
      setFailed(true);
    } finally {
      setSubmitting(null);
    }
  };

  if (submittedRunId) return <Link href={`/evolution/${submittedRunId}`} className="text-xs text-bull underline">{t("viewTask")}</Link>;

  return (
    <div className="flex shrink-0 items-center gap-1.5">
      {failed && <span className="font-mono text-[10px] text-fox-red">{t("failed")}</span>}
      <button
        type="button"
        disabled={submitting !== null}
        onClick={() => void respond("deny")}
        className="rounded-md border border-fox-red/35 px-2 py-1 font-mono text-[10px] uppercase text-fox-red disabled:opacity-40"
      >
        {submitting === "deny" ? t("working") : t("deny")}
      </button>
      <button
        type="button"
        disabled={submitting !== null}
        onClick={() => void respond("allow")}
        className="rounded-md border border-bull/35 bg-bull/10 px-2 py-1 font-mono text-[10px] uppercase text-bull disabled:opacity-40"
      >
        {submitting === "allow" ? t("working") : t("allow")}
      </button>
    </div>
  );
}
