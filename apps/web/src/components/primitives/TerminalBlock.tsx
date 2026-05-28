"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useReducedMotion } from "motion/react";

import { cn } from "@/lib/cn";
import { TYPEWRITER_MS_PER_CHAR } from "@/lib/motion";

interface TerminalBlockProps {
  /** 可选的全局 prompt 前缀（kit 页面用）。Engineering harness 留空，让样本自带 `$` / `→`。 */
  prompt?: string;
  /**
   * 渲染的内容。string 当一行；array 当多行。
   * typewriter 模式逐字打完整段（按 \n 重排）。
   */
  content: string | string[];
  /** Replay-able typewriter animation. 默认 off。 */
  typewriter?: boolean;
  /** Pin to viewport while parent section scrolls past. */
  scrollPin?: boolean;
  /** Caption above chrome（e.g. file name + ADR ref）。 */
  caption?: string;
  /**
   * 锁定最小行数高度。caller 切换不同 content 时传一个固定的 max 行数，
   * 终端高度就不会随内容变化而跳动。默认按 content 自身行数算。
   */
  minLines?: number;
  className?: string;
}

/**
 * macOS-chrome 终端块。
 *
 * 配色（D-9 终端化重写）：
 *   - 背景：近黑 `#04060d` + cyan 细边框 + 上方 chrome 一段微凸起
 *   - chrome 三色点保留品牌色（不学 macOS 灰）
 *   - 右上角小指示器：typewriter 跑时是 `bull` 脉冲 + "EXEC"，跑完转 "IDLE"
 *   - 行内 token 高亮：注释 muted、字符串 gold、数字 bull、状态词
 *     (`ok/pass/approved` → bull / `deny/error/errored` → fox-red /
 *     `[REDACTED]` → fox-red)、prefix `$` 与 `→` cyan
 *
 * 动画：
 *   - typewriter 默认 off；开启后**每次 content 变化都重新打字**（HMR / chip
 *     切换友好）；末尾常驻 caret 闪烁直到打完
 *   - prefers-reduced-motion 时一次性显示完整文本（不打字、无 caret）
 */
export function TerminalBlock({
  prompt = "",
  content,
  typewriter = false,
  scrollPin = false,
  caption,
  minLines,
  className,
}: TerminalBlockProps) {
  const lines = Array.isArray(content) ? content : [content];
  const fullText = lines.join("\n");
  const reduced = useReducedMotion();
  const [shown, setShown] = useState(typewriter && !reduced ? "" : fullText);
  const intervalRef = useRef<number | null>(null);

  useEffect(() => {
    // 每次 content 切换：清理旧 interval 再重新打字
    if (intervalRef.current !== null) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    if (!typewriter || reduced) {
      setShown(fullText);
      return;
    }
    setShown("");
    let i = 0;
    intervalRef.current = window.setInterval(() => {
      i += 1;
      setShown(fullText.slice(0, i));
      if (i >= fullText.length && intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    }, TYPEWRITER_MS_PER_CHAR);
    return () => {
      if (intervalRef.current !== null) {
        window.clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [typewriter, reduced, fullText]);

  const isTyping = typewriter && !reduced && shown.length < fullText.length;
  const shownLines = useMemo(() => shown.split("\n"), [shown]);
  // 锁定 pre 最小高度。优先用 caller 传的 minLines（让多个不同 content
  // 之间高度一致），否则按当前 content 行数。1.6em 是 leading-[1.6]。
  const fullLineCount = useMemo(() => fullText.split("\n").length, [fullText]);
  const heightLines = Math.max(fullLineCount, minLines ?? 0);
  const preMinHeight = `${heightLines * 1.6}em`;

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-cyan/15 bg-[#04060d] font-mono text-sm shadow-[0_0_0_1px_rgba(95,179,255,0.05),0_24px_48px_-24px_rgba(95,179,255,0.18)]",
        scrollPin && "sticky top-24",
        className,
      )}
    >
      {/* chrome bar */}
      <div className="flex items-center gap-2 border-b border-cyan/12 bg-[#080c18] px-4 py-2.5">
        <span aria-hidden className="size-2.5 rounded-full bg-fox-red/75" />
        <span aria-hidden className="size-2.5 rounded-full bg-gold/75" />
        <span aria-hidden className="size-2.5 rounded-full bg-bull/75" />
        {caption ? (
          <span className="ml-3 font-mono text-[11px] tracking-tight text-fg-muted/85">
            {caption}
          </span>
        ) : null}
        <span className="ml-auto flex items-center gap-1.5 font-mono text-[9.5px] uppercase tracking-[0.22em]">
          <span
            aria-hidden
            className={cn(
              "size-1.5 rounded-full transition-colors",
              isTyping ? "bg-bull animate-pulse" : "bg-fg-muted/30",
            )}
          />
          <span className={isTyping ? "text-bull/80" : "text-fg-muted/45"}>
            {isTyping ? "exec" : "idle"}
          </span>
        </span>
      </div>

      {/* body —— min-height 锁住，避免 typewriter 期间高度跳动 */}
      <pre
        className="overflow-x-auto px-4 py-3 leading-[1.6] text-fg/90"
        style={{ minHeight: preMinHeight }}
      >
        <code className="block">
          {prompt ? (
            <>
              <span className="select-none text-cyan/75">{prompt}</span>{" "}
            </>
          ) : null}
          {shownLines.map((line, idx) => (
            <span key={idx} className="block">
              {renderTokens(line)}
              {isTyping && idx === shownLines.length - 1 ? (
                <span
                  aria-hidden
                  className="ml-0.5 inline-block h-[1em] w-[0.6em] translate-y-[2px] bg-cyan/85 align-middle caret-blink"
                />
              ) : null}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Tokenizer —— 行级 + 行内组合
// ───────────────────────────────────────────────────────────────────

interface TokenSpan {
  text: string;
  cls?: string;
}

/** 整行 prefix 类型（`#` / `//` / `$` / `→`），剩余部分继续走 inline tokenizer。 */
function renderTokens(line: string): ReactNode {
  // 整行注释
  if (/^\s*(\/\/|#)/.test(line)) {
    return <span className="italic text-fg-muted/55">{line || " "}</span>;
  }

  // shell prompt
  const dollarMatch = line.match(/^(\s*)\$\s?/);
  if (dollarMatch) {
    const indent = dollarMatch[1];
    const rest = line.slice(dollarMatch[0].length);
    return (
      <>
        {indent}
        <span className="text-cyan/80">$</span>{" "}
        <span className="text-fg">{inlineTokens(rest)}</span>
      </>
    );
  }

  // output arrow
  const arrowMatch = line.match(/^(\s*)→\s?/);
  if (arrowMatch) {
    const indent = arrowMatch[1];
    const rest = line.slice(arrowMatch[0].length);
    return (
      <>
        {indent}
        <span className="text-cyan/80">→</span>{" "}
        <span className="text-fg/85">{inlineTokens(rest)}</span>
      </>
    );
  }

  // 空行保留高度
  if (line === "") return " ";

  return inlineTokens(line);
}

/**
 * 行内 token 高亮：状态词 / 字符串字面量 / 数字 / [REDACTED]。
 *
 * 单 regex 取并集，按 group index 决定 className。其余文本走默认色（继承 pre 的 fg/90）。
 */
const INLINE_TOKEN_RE = new RegExp(
  [
    "(\\[REDACTED\\])", // 1: redacted
    "(\\balpha\\s+✓|\\b(?:ok|pass|approved|true)\\b)", // 2: positive status
    "(\\b(?:deny|denied|errored|failed?|reject(?:ed)?|false)\\b)", // 3: negative status
    '("[^"\\n]*"|\'[^\'\\n]*\')', // 4: string literal
    "(\\b\\d+(?:\\.\\d+)?(?:[smhd]|ms|s)?\\b)", // 5: number (optional time unit)
  ].join("|"),
  "g",
);

function inlineTokens(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(INLINE_TOKEN_RE)) {
    const start = m.index!;
    if (start > last) out.push(text.slice(last, start));
    const matched = m[0];
    let cls = "";
    if (m[1]) cls = "text-fox-red font-medium";
    else if (m[2]) cls = "text-bull";
    else if (m[3]) cls = "text-fox-red";
    else if (m[4]) cls = "text-gold/85";
    else if (m[5]) cls = "text-bull/85";
    out.push(
      <span key={`t${key++}`} className={cls}>
        {matched}
      </span>,
    );
    last = start + matched.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
