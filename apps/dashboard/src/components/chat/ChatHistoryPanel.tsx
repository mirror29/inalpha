"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/cn";

/** 历史会话摘要。 */
export interface ThreadSummary {
  id: string;
  title: string | null;
  updatedAt: string;
}

/**
 * 历史会话下拉面板。
 *
 * 纯展示组件，状态由父组件（ChatThread）管理。
 */
export function ChatHistoryPanel({
  open,
  threads,
  historyError,
  currentThreadId,
  sourceDownLabel,
  untitledLabel,
  onSwitch,
  onReload,
}: {
  open: boolean;
  threads: ThreadSummary[] | null;
  historyError: boolean;
  currentThreadId: string;
  sourceDownLabel: string;
  untitledLabel: string;
  onSwitch: (threadId: string) => void;
  onReload: (threadId: string) => void;
}) {
  const t = useTranslations("chat");

  if (!open) return null;

  return (
    <div className="absolute left-0 right-0 top-full z-20 mx-3 mt-0.5 max-h-64 overflow-y-auto rounded-lg border border-border-subtle bg-bg-elev shadow-lg">
      {threads === null ? (
        <p className="px-3 py-3 text-center font-mono text-xs text-fg-muted">
          {t("loadingHistory")}
        </p>
      ) : threads.length === 0 ? (
        <p className="px-3 py-3 text-center font-mono text-xs text-fg-muted">
          {t("historyEmpty")}
        </p>
      ) : (
        threads.map((th) => (
          <button
            key={th.id}
            type="button"
            onClick={() => {
              if (th.id === currentThreadId) onReload(th.id);
              else onSwitch(th.id);
            }}
            className={cn(
              "flex w-full items-center justify-between gap-2 border-b border-border-subtle/60 px-3 py-2 text-left text-xs transition-colors last:border-b-0 hover:bg-bg/60",
              th.id === currentThreadId ? "bg-cyan/10 text-fg" : "text-fg-muted",
            )}
          >
            <span className="truncate">
              {th.title || untitledLabel}
            </span>
            <span className="shrink-0 font-mono text-[10px] text-fg-muted/70 tabular-nums">
              {th.updatedAt.slice(0, 10)}
            </span>
          </button>
        ))
      )}
      {historyError && (
        <p className="border-t border-border-subtle px-3 py-2 text-center font-mono text-[10px] text-fox-red/70">
          {sourceDownLabel}
        </p>
      )}
    </div>
  );
}
