import * as React from "react";

import { cn } from "@/lib/cn";

interface PanelProps {
  title: string;
  /** 标题右侧的附属信息(计数 / 操作)。 */
  aside?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}

/**
 * 看板里的内容分区 —— hairline 边框 + 标题,统一各 section 的外观。
 */
export function Panel({ title, aside, className, children }: PanelProps) {
  return (
    <section
      className={cn(
        "rise rounded-xl border border-border-subtle bg-bg-elev/30 backdrop-blur-sm transition-colors hover:border-border-subtle/80",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div className="flex items-center gap-2.5">
          {/* 朱红印章刻度 —— 每个 section 的品牌锚点。 */}
          <span className="h-3.5 w-0.5 shrink-0 rounded-full bg-seal/70" />
          <h2 className="whitespace-nowrap font-mono text-xs uppercase tracking-[0.16em] text-fg-muted">
            {title}
          </h2>
        </div>
        {aside}
      </header>
      {children}
    </section>
  );
}
