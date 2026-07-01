"use client";

import { useLayoutEffect, useRef } from "react";
import { useTranslations } from "next-intl";

import { ChatMessage, type AGMessage } from "./ChatMessage";

/**
 * 消息列表 + 自动滚动 + 思考中指示器。
 */
export function ChatMessageList({
  messages,
  toolNames,
  resolvedToolCallIds,
  isLoading,
  historyLoading,
  toolDone,
  toolResultLabel,
  emptyLabel,
  thinkingLabel,
  loadingHistoryLabel,
}: {
  messages: AGMessage[];
  toolNames: Map<string, string>;
  resolvedToolCallIds: Set<string>;
  isLoading: boolean;
  historyLoading: boolean;
  toolDone: string;
  toolResultLabel: string;
  emptyLabel: string;
  thinkingLabel: string;
  loadingHistoryLabel: string;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, isLoading]);

  const visible = messages.filter(
    (m) => m.role === "user" || m.role === "assistant" || m.role === "tool",
  );

  return (
    <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
      {historyLoading ? (
        <div className="flex items-center gap-2 px-1 font-mono text-xs text-fg-muted">
          <span className="size-1.5 rounded-full bg-cyan caret-blink" />
          {loadingHistoryLabel}
        </div>
      ) : visible.length === 0 ? (
        <p className="mt-8 px-2 text-center text-sm leading-relaxed text-fg-muted">
          {emptyLabel}
        </p>
      ) : (
        visible.map((m, i) => (
          <ChatMessage
            key={m.id}
            message={m}
            toolNames={toolNames}
            resolvedToolCallIds={resolvedToolCallIds}
            toolDone={toolDone}
            toolResultLabel={toolResultLabel}
            isStreaming={
              isLoading &&
              i === visible.length - 1 &&
              m.role === "assistant"
            }
          />
        ))
      )}
      {isLoading && !historyLoading && (
        <div className="flex items-center gap-2 px-1 font-mono text-xs text-fg-muted">
          <span className="size-1.5 rounded-full bg-cyan caret-blink" />
          {thinkingLabel}
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}
