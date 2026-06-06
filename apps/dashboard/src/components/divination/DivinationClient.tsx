"use client";

import { CopilotKit, useCopilotChatInternal } from "@copilotkit/react-core";
import { Sparkles } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { cn } from "@/lib/cn";
import { DivinationCard } from "./DivinationCard";
import { isDivinationTool, parseDivination, type DivinationView } from "./types";

/** AG-UI 消息最小形态(与 ChatThread 一致)。 */
type AGMessage = {
  id: string;
  role: string;
  content?: unknown;
  toolCalls?: { id: string; function?: { name?: string } }[];
  toolCallId?: string;
};

/** 抽出可显示纯文本(string / 多模态数组)。 */
function textOf(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((p) => (p && typeof p === "object" && "text" in p ? String(p.text ?? "") : ""))
      .join("");
  }
  return "";
}

type Mode = "hexagram" | "tarotSingle" | "tarotThree";

/** 占卜台正文 —— 在 CopilotKit 上下文内,驱动同一 orchestrator agent。 */
function DivinationConsole() {
  const t = useTranslations("divination");
  const locale = useLocale();
  const hook = useCopilotChatInternal();
  const messages = (hook.messages ?? []) as unknown as AGMessage[];
  const { sendMessage, isLoading } = hook;
  const [question, setQuestion] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  // toolCallId → tool 名(tool-result 消息只带 id)。
  const toolNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of messages) {
      if (m.toolCalls) for (const c of m.toolCalls) map.set(c.id, c.function?.name ?? "");
    }
    return map;
  }, [messages]);

  // 最近一条玄学结果(倒序找第一条可解析的 divination tool-result)。
  const latest = useMemo<DivinationView | null>(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.role !== "tool") continue;
      if (!isDivinationTool(toolNames.get(m.toolCallId ?? ""))) continue;
      const reading = parseDivination(textOf(m.content));
      if (reading) return reading;
    }
    return null;
  }, [messages, toolNames]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [latest, isLoading]);

  const cast = (mode: Mode) => {
    if (isLoading) return;
    const q = question.trim() || t("defaultQuestion");
    const key =
      mode === "hexagram"
        ? "promptHexagram"
        : mode === "tarotSingle"
          ? "promptTarotSingle"
          : "promptTarotThree";
    void sendMessage({
      id: crypto.randomUUID(),
      role: "user",
      content: t(key, { question: q }),
    } as Parameters<typeof sendMessage>[0]);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      cast("hexagram");
    }
  };

  // 最近一条 assistant 文本(玄学旁白),给卡片配一句解读。
  const narrative = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.role === "assistant") {
        const txt = textOf(m.content).trim();
        if (txt) return txt;
      }
    }
    return "";
  }, [messages]);

  return (
    <div className="flex flex-col gap-5">
      {/* 输入 + 起卦按钮 */}
      <div className="flex flex-col gap-3 rounded-xl border border-border-subtle bg-bg-elev/30 p-4 backdrop-blur-sm">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={t("questionPlaceholder")}
          lang={locale}
          className="w-full rounded-lg border border-border-subtle bg-bg/60 px-3 py-2.5 text-sm text-fg outline-none transition-colors placeholder:text-fg-muted/60 focus:border-cyan/50"
        />
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => cast("hexagram")}
            disabled={isLoading}
            className="flex items-center gap-1.5 rounded-md bg-seal px-3 py-1.5 text-sm text-bg-deep transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            <Sparkles className="size-3.5" strokeWidth={2} />
            {t("castHexagram")}
          </button>
          <button
            type="button"
            onClick={() => cast("tarotSingle")}
            disabled={isLoading}
            className="rounded-md border border-border-subtle bg-bg/60 px-3 py-1.5 text-sm text-fg transition-colors hover:border-cyan/40 disabled:opacity-40"
          >
            {t("drawTarotSingle")}
          </button>
          <button
            type="button"
            onClick={() => cast("tarotThree")}
            disabled={isLoading}
            className="rounded-md border border-border-subtle bg-bg/60 px-3 py-1.5 text-sm text-fg transition-colors hover:border-cyan/40 disabled:opacity-40"
          >
            {t("drawTarotThree")}
          </button>
        </div>
      </div>

      {/* 结果区 */}
      {isLoading && (
        <div className="flex items-center gap-2 px-1 font-mono text-xs text-fg-muted">
          <span className="size-1.5 rounded-full bg-cyan caret-blink" />
          {t("divining")}
        </div>
      )}
      {latest ? (
        <div className="flex flex-col gap-3">
          <DivinationCard reading={latest} />
          {narrative && (
            <div className="max-w-2xl rounded-lg border border-border-subtle bg-bg-deep/40 px-4 py-3 text-sm leading-relaxed text-fg-muted">
              {narrative}
            </div>
          )}
        </div>
      ) : (
        !isLoading && (
          <p className={cn("px-1 text-sm leading-relaxed text-fg-muted")}>{t("emptyHint")}</p>
        )
      )}
      <div ref={endRef} />
    </div>
  );
}

const LS_DIVINATION_THREAD = "inalpha-divination-thread";

/**
 * 占卜台(独立趣味页)。
 *
 * 用**独立 threadId** 包一层自己的 CopilotKit provider(与右下角对话栏互不干扰),
 * 通过同一 orchestrator agent 调 `divination.*` tool —— 单一引擎、单一卡片组件,
 * 不重复任何卦表 / 牌库。
 */
export function DivinationClient() {
  const t = useTranslations("divination");
  const [threadId, setThreadId] = useState<string | null>(null);

  // SSR 安全:mount 后从 localStorage 读 / 生成稳定 threadId。
  useEffect(() => {
    let id = localStorage.getItem(LS_DIVINATION_THREAD);
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem(LS_DIVINATION_THREAD, id);
    }
    setThreadId(id);
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center gap-3">
        <span className="h-7 w-1 shrink-0 rounded-full bg-seal" />
        <div>
          <h1 className="font-display text-2xl text-fg">{t("pageTitle")}</h1>
          <p className="mt-1 text-sm text-fg-muted">{t("pageSubtitle")}</p>
        </div>
      </header>

      {threadId && (
        <CopilotKit
          runtimeUrl="/api/copilotkit"
          agent="orchestrator"
          threadId={threadId}
          showDevConsole={false}
          enableInspector={false}
        >
          <DivinationConsole />
        </CopilotKit>
      )}
    </div>
  );
}
