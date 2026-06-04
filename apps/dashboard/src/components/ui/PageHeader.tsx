import * as React from "react";

/**
 * 看板页头 —— 编辑体序号 + 标题 + 副标题,右侧挂状态条/操作。各看板统一观感。
 */
export function PageHeader({
  index,
  title,
  subtitle,
  right,
}: {
  index: string;
  title: string;
  subtitle?: string;
  right?: React.ReactNode;
}) {
  return (
    <header className="flex flex-col gap-4 border-b border-border-subtle pb-5 lg:flex-row lg:items-end lg:justify-between">
      <div>
        <div className="flex items-baseline gap-3">
          {/* 朱红印章刻度 + 编辑体序号 —— 终端页眉的品牌签名。 */}
          <span className="h-6 w-1 shrink-0 self-center rounded-full bg-seal" />
          <span className="font-display text-lg italic text-fg-muted/70">
            {index}
          </span>
          <h1 className="font-display text-3xl text-fg lg:text-4xl">{title}</h1>
        </div>
        {subtitle && (
          <p className="mt-2 max-w-xl text-sm text-fg-muted">{subtitle}</p>
        )}
      </div>
      {right}
    </header>
  );
}
