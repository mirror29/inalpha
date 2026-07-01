"use client";

import { DivinationCard } from "@/components/divination/DivinationCard";
import { isDivinationTool, parseDivination } from "@/components/divination/types";
import { stripPageContext } from "@/lib/page-context";
import { ChatMarkdown } from "./ChatMarkdown";
import { ChatStreamdown } from "./ChatStreamdown";
import { ChatToolChip } from "./ChatToolChip";
import { inferToolState, TOOL_STATE_MAP } from "./tool-states";

/** AG-UI 消息最小形态。 */
export type AGMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool" | "reasoning" | string;
  content?: unknown;
  toolCalls?: { id: string; function?: { name?: string; arguments?: string } }[];
  toolCallId?: string;
};

/** AG-UI content 兼容 string / 多模态数组 → 可显示纯文本。 */
function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((p) =>
        p && typeof p === "object" && "text" in p ? String(p.text ?? "") : "",
      )
      .join("");
  }
  return "";
}

/**
 * 单条消息渲染：用户气泡 / agent 文本 + 工具 chip / 工具结果。
 */
export function ChatMessage({
  message,
  toolNames,
  resolvedToolCallIds,
  toolDone,
  toolResultLabel,
  isStreaming,
}: {
  message: AGMessage;
  toolNames: Map<string, string>;
  resolvedToolCallIds: Set<string>;
  toolDone: string;
  toolResultLabel: string;
  isStreaming?: boolean;
}) {
  const text = textOf(message.content);

  if (message.role === "user") {
    return (
      <div className="rise flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg rounded-br-sm bg-cyan/10 px-3 py-2 text-sm text-fg">
          {stripPageContext(text)}
        </div>
      </div>
    );
  }

  if (message.role === "tool") {
    const toolName = toolNames.get(message.toolCallId ?? "") ?? "tool";
    if (isDivinationTool(toolName)) {
      const reading = parseDivination(text);
      if (reading) {
        return (
          <div className="flex justify-start">
            <DivinationCard reading={reading} className="max-w-[95%]" />
          </div>
        );
      }
    }
    const hasError = (() => {
      try {
        const parsed = JSON.parse(text);
        return Boolean(parsed?.isError || parsed?.error);
      } catch {
        return false;
      }
    })();
    const state = inferToolState(true, hasError);
    const { label: stateLabel } = TOOL_STATE_MAP[state];

    return (
      <div className="rise flex justify-start">
        <ChatToolChip
          name={toolName}
          result={text}
          resultLabel={toolResultLabel}
          state={state}
          stateLabel={stateLabel}
        />
      </div>
    );
  }

  // assistant
  const calls = (message.toolCalls ?? []).filter(
    (c) => !resolvedToolCallIds.has(c.id),
  );
  if (!text && calls.length === 0) return null;

  return (
    <div className="rise flex flex-col items-start gap-1.5">
      {text && (
        <div className="max-w-[90%] break-words rounded-lg rounded-bl-sm bg-bg-deep/60 px-3 py-2 text-sm leading-relaxed text-fg">
          {isStreaming ? (
            <ChatStreamdown streaming>{text}</ChatStreamdown>
          ) : (
            <ChatMarkdown>{text}</ChatMarkdown>
          )}
        </div>
      )}
      {calls.map((c) => {
        const runningState = inferToolState(false);
        const { label: runningLabel } = TOOL_STATE_MAP[runningState];
        return (
          <ChatToolChip
            key={c.id}
            name={c.function?.name ?? "tool"}
            resultLabel={toolResultLabel}
            state={runningState}
            stateLabel={runningLabel}
          />
        );
      })}
    </div>
  );
}
