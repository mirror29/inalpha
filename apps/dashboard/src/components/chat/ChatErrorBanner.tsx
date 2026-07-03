"use client";

import { TriangleAlert, X } from "lucide-react";

/**
 * Agent 错误横幅。
 *
 * 上游 LLM 报错 / 流中断时在对话栏顶部显示红字提示，
 * 用于区分"agent 在思考"和"真的出错了"。
 */
export function ChatErrorBanner({
  error,
  onDismiss,
  dismissLabel,
}: {
  error: string;
  onDismiss: () => void;
  dismissLabel: string;
}) {
  return (
    <div className="mx-3 mb-2 flex items-start gap-2 rounded-lg border border-fox-red/40 bg-fox-red/10 px-3 py-2 text-xs text-fox-red">
      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" strokeWidth={2} />
      <span className="min-w-0 flex-1 break-words">{error}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label={dismissLabel}
        title={dismissLabel}
        className="shrink-0 text-fox-red/70 transition-colors hover:text-fox-red"
      >
        <X className="size-3.5" strokeWidth={2} />
      </button>
    </div>
  );
}
