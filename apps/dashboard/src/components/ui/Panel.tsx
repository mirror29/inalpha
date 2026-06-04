import * as React from "react";

import { cn } from "@/lib/cn";

interface PanelProps {
  /** 编辑体序号,如 "01" —— broadsheet 调性。 */
  index?: string;
  title: string;
  /** 标题右侧的附属信息(计数 / 操作)。 */
  aside?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}

/**
 * 看板里的内容分区 —— hairline 边框 + 序号 + 标题,统一各 section 的外观。
 */
export function Panel({ index, title, aside, className, children }: PanelProps) {
  return (
    <section
      className={cn(
        "rounded-xl border border-border-subtle bg-bg-elev/30 backdrop-blur-sm",
        className,
      )}
    >
      <header className="flex items-center justify-between gap-3 border-b border-border-subtle px-4 py-3">
        <div className="flex items-baseline gap-2.5">
          {index && (
            <span className="font-display text-sm italic text-fg-muted/70">
              {index}
            </span>
          )}
          <h2 className="font-mono text-xs uppercase tracking-[0.16em] text-fg-muted">
            {title}
          </h2>
        </div>
        {aside}
      </header>
      {children}
    </section>
  );
}
