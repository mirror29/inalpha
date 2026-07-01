"use client";

import { MapPin, SendHorizontal, Square, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { type KeyboardEvent, useCallback, useEffect, useRef } from "react";

import { cn } from "@/lib/cn";

/**
 * 对话输入区域：输入框 + 发送/停止按钮 + 页面上下文胶囊。
 *
 * 所有状态由父组件（ChatThread）管理，通过 props 传入。
 */
export function ChatInput({
  draft,
  isLoading,
  contextAttached,
  contextKind,
  contextId,
  onDraftChange,
  onSubmit,
  onStop,
  onContextDismiss,
}: {
  draft: string;
  isLoading: boolean;
  contextAttached: boolean;
  contextKind: string;
  contextId?: string;
  onDraftChange: (v: string) => void;
  onSubmit: () => void;
  onStop: () => void;
  onContextDismiss: () => void;
}) {
  const t = useTranslations("chat");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (draft === "") textareaRef.current?.focus();
  }, [draft]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        onSubmit();
      }
    },
    [onSubmit],
  );

  return (
    <div className="border-t border-border-subtle p-3">
      {contextAttached && (
        <div className="mb-2 flex min-w-0 items-center gap-1 rounded-full border border-border-subtle bg-bg/60 py-0.5 pl-2 pr-1 text-[11px] text-fg-muted">
          <MapPin className="size-3 shrink-0 text-cyan" strokeWidth={2} />
          <span className="truncate text-fg">
            {t(`context.kind.${contextKind}`)}
          </span>
          {contextId && (
            <span className="shrink-0 font-mono text-fg-muted/70 tabular-nums">
              {contextId.slice(0, 8)}
            </span>
          )}
          <button
            type="button"
            onClick={onContextDismiss}
            aria-label={t("context.dismiss")}
            title={t("context.dismiss")}
            className="shrink-0 rounded-full p-0.5 text-fg-muted/70 transition-colors hover:bg-bg-elev/60 hover:text-fg"
          >
            <X className="size-3" strokeWidth={2} />
          </button>
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={t("placeholder")}
          rows={1}
          className={cn(
            "min-h-[36px] max-h-32 flex-1 resize-none rounded-md border border-border-subtle bg-bg-deep/60 px-3 py-2 text-sm text-fg placeholder:text-fg-muted/50 focus:border-cyan/40 focus:outline-none",
            isLoading && "opacity-50",
          )}
        />
        {isLoading ? (
          <button
            type="button"
            onClick={onStop}
            title={t("stop")}
            className="flex size-9 shrink-0 items-center justify-center rounded-md border border-cyan/30 bg-cyan/10 text-cyan transition-colors hover:bg-cyan/20"
          >
            <Square className="size-4" strokeWidth={2} />
          </button>
        ) : (
          <button
            type="button"
            onClick={onSubmit}
            title={t("send")}
            disabled={!draft.trim()}
            className="flex size-9 shrink-0 items-center justify-center rounded-md border border-seal/30 bg-seal/10 text-seal transition-colors hover:bg-seal/20 disabled:opacity-30"
          >
            <SendHorizontal className="size-4" strokeWidth={2} />
          </button>
        )}
      </div>
    </div>
  );
}
