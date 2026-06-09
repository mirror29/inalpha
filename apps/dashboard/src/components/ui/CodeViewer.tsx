"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

import { cn } from "@/lib/cn";

/**
 * 只读代码查看器 —— 编辑器外观(头部栏 + 行号槽 + 等宽正文) + 一键复制。
 *
 * 零依赖(不引语法高亮库,保持 bundle 轻量,与 ChatMarkdown 同思路)。垂直滚动在外层、
 * 水平滚动只在代码区 → 行号槽在左侧固定不随横向滚动跑掉。
 *
 * @param code 源码全文
 * @param lang 语言标签(只展示,如 "python")
 * @param copyLabel / copiedLabel 复制按钮文案(由调用方传本地化串)
 */
export function CodeViewer({
  code,
  lang,
  copyLabel = "Copy",
  copiedLabel = "Copied",
  className,
}: {
  code: string;
  lang?: string;
  copyLabel?: string;
  copiedLabel?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const lines = code.split("\n");

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // 剪贴板不可用(非安全上下文 / 权限拒绝)—— 静默,不打断阅读。
    }
  };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-lg border border-border-subtle bg-bg-deep",
        className,
      )}
    >
      {/* 头部栏:语言标签 + 复制按钮 */}
      <div className="flex items-center justify-between border-b border-border-subtle bg-bg-elev/40 px-3 py-1.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted/70">
          {lang ?? "code"}
        </span>
        <button
          type="button"
          onClick={onCopy}
          className={cn(
            "flex items-center gap-1.5 rounded-md px-2 py-1 font-mono text-[11px] transition-colors",
            copied
              ? "text-bull"
              : "text-fg-muted hover:bg-bg/60 hover:text-fg",
          )}
        >
          {copied ? (
            <Check className="size-3.5" strokeWidth={2} />
          ) : (
            <Copy className="size-3.5" strokeWidth={1.75} />
          )}
          {copied ? copiedLabel : copyLabel}
        </button>
      </div>

      {/* 正文:行号槽(左固定) + 代码(可横向滚动);垂直滚动在外层让两者同步。 */}
      <div className="flex max-h-[28rem] overflow-y-auto">
        <div
          aria-hidden
          className="shrink-0 select-none border-r border-border-subtle/60 px-3 py-3 text-right font-mono text-[12px] leading-relaxed text-fg-muted/35 tabular-nums"
        >
          {lines.map((_, i) => (
            <div key={i}>{i + 1}</div>
          ))}
        </div>
        <pre className="min-w-0 flex-1 overflow-x-auto px-3 py-3 font-mono text-[12px] leading-relaxed text-fg-muted">
          <code>{code}</code>
        </pre>
      </div>
    </div>
  );
}
