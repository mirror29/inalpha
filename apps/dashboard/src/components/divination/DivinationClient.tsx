"use client";

import { AnimatePresence, motion } from "motion/react";
import { MessageSquare, Sparkles } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState, type KeyboardEvent } from "react";
import useSWR from "swr";

import { cn } from "@/lib/cn";
import { DivinationCard } from "./DivinationCard";
import type { DivinationView } from "./types";

/** 占卜形态 —— 与后端 `divination/api.ts` 的 mode 一致。 */
type Mode = "hexagram" | "tarotSingle" | "tarotThree";

/** 一条占卜记录(BFF `/api/divination` 返回,createdAt 为 ISO 串)。 */
interface DivinationRecord {
  id: string;
  mode: Mode;
  question: string;
  symbol: string | null;
  kind: "hexagram" | "tarot";
  reading: DivinationView;
  createdAt: string;
}

const fetcher = (url: string) => fetch(url).then((r) => r.json());

/** 从结果里提炼一句摘要(给「去对话栏深聊」的 prompt 用)。 */
function summarize(reading: DivinationView): string {
  if (reading.kind === "hexagram") {
    return reading.changed
      ? `${reading.primary.name} → ${reading.changed.name}`
      : reading.primary.name;
  }
  return reading.cards.map((c) => c.name).join("、");
}

/**
 * 占卜台(独立趣味模块)。
 *
 * **不走 agent 会话**:点按钮直接 `POST /api/divination` 由后端纯函数引擎确定性出卦,
 * 瞬时渲染 + 动画,结果落服务端;历史记录可回看。会话式深度解读由用户主动点
 * 「去对话栏深聊此卦」触发 —— 派发 `inalpha:divination-consult` 事件交给右下角对话栏,
 * 保持「占卜在模块、深聊在 agent、且由用户主动」的边界。
 */
export function DivinationClient() {
  const t = useTranslations("divination");
  const locale = useLocale();
  const [question, setQuestion] = useState("");
  const [current, setCurrent] = useState<DivinationRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  const { data, mutate } = useSWR<{ records: DivinationRecord[] }>(
    "/api/divination/history?limit=30",
    fetcher,
  );
  const history = data?.records ?? [];

  const cast = async (mode: Mode) => {
    if (loading) return;
    const q = question.trim() || t("defaultQuestion");
    setLoading(true);
    setError(false);
    try {
      const res = await fetch("/api/divination", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, question: q }),
      });
      if (!res.ok) throw new Error(`cast failed: ${res.status}`);
      const record = (await res.json()) as DivinationRecord;
      setCurrent(record);
      void mutate();
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void cast("hexagram");
    }
  };

  /** 把当前卦象交给对话栏深聊(用户主动触发的 LLM 解读)。 */
  const consult = (record: DivinationRecord) => {
    const prompt = t("consultPrompt", {
      question: record.question,
      summary: summarize(record.reading),
    });
    window.dispatchEvent(
      new CustomEvent("inalpha:divination-consult", { detail: { prompt } }),
    );
  };

  return (
    <div className="flex flex-col gap-6">
      <header className="flex items-center gap-3">
        <span className="h-7 w-1 shrink-0 rounded-full bg-seal" />
        <div>
          <h1 className="font-display text-2xl text-fg">{t("pageTitle")}</h1>
          <p className="mt-1 text-sm text-fg-muted">{t("pageSubtitle")}</p>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* 左:起卦 + 结果 */}
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
                disabled={loading}
                className="flex items-center gap-1.5 rounded-md bg-seal px-3 py-1.5 text-sm text-bg-deep transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                <Sparkles className="size-3.5" strokeWidth={2} />
                {t("castHexagram")}
              </button>
              <button
                type="button"
                onClick={() => cast("tarotSingle")}
                disabled={loading}
                className="rounded-md border border-border-subtle bg-bg/60 px-3 py-1.5 text-sm text-fg transition-colors hover:border-cyan/40 disabled:opacity-40"
              >
                {t("drawTarotSingle")}
              </button>
              <button
                type="button"
                onClick={() => cast("tarotThree")}
                disabled={loading}
                className="rounded-md border border-border-subtle bg-bg/60 px-3 py-1.5 text-sm text-fg transition-colors hover:border-cyan/40 disabled:opacity-40"
              >
                {t("drawTarotThree")}
              </button>
            </div>
          </div>

          {/* 结果区 */}
          {loading && (
            <div className="flex items-center gap-2 px-1 font-mono text-xs text-fg-muted">
              <span className="size-1.5 rounded-full bg-cyan caret-blink" />
              {t("divining")}
            </div>
          )}
          {error && (
            <p className="rounded-lg border border-seal/40 bg-seal/5 px-4 py-3 text-sm text-fg-muted">
              {t("castError")}
            </p>
          )}

          <AnimatePresence mode="wait">
            {current && !loading && (
              <motion.div
                key={current.id}
                initial={{ opacity: 0, y: 16, filter: "blur(6px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.45, ease: [0.22, 1, 0.36, 1] }}
                className="flex flex-col gap-3"
              >
                <DivinationCard reading={current.reading} />
                <button
                  type="button"
                  onClick={() => consult(current)}
                  className="flex w-fit items-center gap-1.5 rounded-md border border-cyan/40 bg-cyan/5 px-3 py-1.5 text-sm text-cyan transition-colors hover:bg-cyan/10"
                >
                  <MessageSquare className="size-3.5" strokeWidth={1.75} />
                  {t("consultInChat")}
                </button>
              </motion.div>
            )}
          </AnimatePresence>

          {!current && !loading && !error && (
            <p className="px-1 text-sm leading-relaxed text-fg-muted">{t("emptyHint")}</p>
          )}
        </div>

        {/* 右:历史占卜记录 */}
        <aside className="flex flex-col gap-3">
          <h2 className="font-mono text-xs uppercase tracking-[0.16em] text-fg-muted">
            {t("historyTitle")}
          </h2>
          {history.length === 0 ? (
            <p className="text-sm text-fg-muted/70">{t("historyEmpty")}</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {history.map((rec) => {
                const isActive = current?.id === rec.id;
                return (
                  <li key={rec.id}>
                    <button
                      type="button"
                      onClick={() => setCurrent(rec)}
                      className={cn(
                        "flex w-full flex-col gap-1 rounded-lg border px-3 py-2 text-left transition-colors",
                        isActive
                          ? "border-seal/50 bg-seal/5"
                          : "border-border-subtle bg-bg-elev/20 hover:border-cyan/30 hover:bg-bg-elev/40",
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-display text-sm text-fg">
                          {summarize(rec.reading)}
                        </span>
                        <span className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-fg-muted/60">
                          {rec.kind === "hexagram" ? t("hexagramTitle") : t("tarotTitle")}
                        </span>
                      </div>
                      <span className="truncate text-xs text-fg-muted">{rec.question}</span>
                      <span className="font-mono text-[10px] text-fg-muted/50">
                        {new Date(rec.createdAt).toLocaleString(locale)}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </aside>
      </div>
    </div>
  );
}
