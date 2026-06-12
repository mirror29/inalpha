"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/cn";

interface CopyableCommandProps {
  command: string;
  copyLabel: string;
  copiedLabel: string;
  className?: string;
}

export function CopyableCommand({
  command,
  copyLabel,
  copiedLabel,
  className,
}: CopyableCommandProps) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable — ignore */
    }
  }

  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border border-border-subtle bg-bg-elev/60 px-4 py-3 font-mono text-sm backdrop-blur",
        className,
      )}
    >
      <span className="select-none text-cyan/70">$</span>
      {/* 移动端命令完整可读（断行），sm 起单行截断交给 Copy 按钮兜底 */}
      <code className="min-w-0 flex-1 break-all text-[12.5px] leading-snug text-fg sm:truncate sm:text-sm">
        {command}
      </code>
      <button
        type="button"
        onClick={handleCopy}
        className="flex items-center gap-1.5 rounded border border-border-subtle bg-bg/60 px-2 py-1 text-xs text-fg-muted transition-colors hover:border-cyan hover:text-cyan"
        aria-label={copyLabel}
      >
        {copied ? (
          <>
            <Check className="size-3.5" />
            {copiedLabel}
          </>
        ) : (
          <>
            <Copy className="size-3.5" />
            {copyLabel}
          </>
        )}
      </button>
    </div>
  );
}
