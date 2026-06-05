"use client";

import { useLocale, useNow, useTranslations } from "next-intl";
import { ChevronRight, MessageSquare } from "lucide-react";

import type { ActivityEvent, ActivityTone } from "@/lib/types";
import { Link } from "@/i18n/navigation";
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
        const clickable = isConversation || Boolean(e.href);
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
                <span className="truncate font-medium text-fg">{e.title}</span>
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
            ) : e.href ? (
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
