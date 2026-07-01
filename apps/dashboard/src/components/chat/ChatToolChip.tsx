"use client";

import { useMemo, useState } from "react";

import { cn } from "@/lib/cn";
import { ToolOutput } from "./ToolOutput";
import { resolveToolView } from "./tool-views";
import { TOOL_STATE_MAP, type ToolState } from "./tool-states";

/** JSON 字符串美化（两格缩进）；不是合法 JSON 原样返回。 */
function pretty(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

/**
 * 工具调用 / 结果的紧凑 chip。
 *
 * 状态机驱动（七态）：
 *   - Running: gold chip + 无展开
 *   - Completed: green chip + 可展开查看结果
 *   - Error: red chip + 展开显示错误
 *   - Denied: orange chip
 *   - Pending: gray chip + pulse 动画
 *   - Awaiting Approval: yellow chip + clock + pulse
 *   - Responded: blue chip
 *
 * 输出按优先级渲染：工具专属视图(tool-views)→ 通用结构化(ToolOutput)→ raw 切换。
 */
export function ChatToolChip({
  name,
  resultLabel,
  result,
  state,
  stateLabel,
}: {
  name: string;
  resultLabel: string;
  result?: string;
  state: ToolState;
  stateLabel: string;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const { Icon, color, expandable, pulse } = TOOL_STATE_MAP[state];

  const view = useMemo(() => {
    if (!result) return null;
    try {
      return resolveToolView(name, JSON.parse(result));
    } catch {
      return null;
    }
  }, [name, result]);

  const sectionCaption =
    "px-2.5 pt-1.5 font-mono text-[9px] uppercase tracking-[0.18em] text-fg-muted/60";

  const head = (
    <>
      <Icon
        className={cn("size-3.5 shrink-0", pulse && "animate-pulse")}
        strokeWidth={1.75}
      />
      <span className="truncate text-fg">{name}</span>
      <span
        className={cn(
          "ml-auto font-mono text-[10px] uppercase tracking-[0.12em]",
          color,
        )}
      >
        {stateLabel}
      </span>
    </>
  );

  if (!expandable || !result) {
    return (
      <div
        className={cn(
          "group flex w-full max-w-[90%] items-center gap-2 rounded-md border border-border-subtle bg-bg/40 px-2.5 py-1.5 font-mono text-xs",
          color,
        )}
      >
        {head}
      </div>
    );
  }

  return (
    <details className="group w-full max-w-[90%] overflow-hidden rounded-md border border-border-subtle bg-bg/40 text-xs transition-colors hover:border-cyan/40 hover:bg-bg-elev/50">
      <summary
        className={cn(
          "flex items-center gap-2 px-2.5 py-1.5 font-mono transition-transform hover:translate-x-0.5 motion-reduce:transition-none",
          color,
        )}
      >
        {head}
      </summary>
      {result && (
        <div className="border-t border-border-subtle bg-bg-deep/50">
          <div className="flex items-baseline justify-between pr-2.5">
            <div className={sectionCaption}>{resultLabel}</div>
            <button
              type="button"
              onClick={() => setShowRaw((v) => !v)}
              className={cn(
                "rounded-sm px-1 font-mono text-[9px] uppercase tracking-[0.18em] transition-colors",
                showRaw
                  ? "text-cyan"
                  : "text-fg-muted/40 hover:text-fg-muted",
              )}
            >
              raw
            </button>
          </div>
          {showRaw ? (
            <pre className="max-h-64 overflow-auto px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-fg-muted">
              {pretty(result)}
            </pre>
          ) : view ? (
            <div className="max-h-72 overflow-auto px-2.5 py-1.5">{view}</div>
          ) : (
            <ToolOutput raw={result} />
          )}
        </div>
      )}
    </details>
  );
}
