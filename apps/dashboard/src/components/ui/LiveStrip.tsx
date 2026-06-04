"use client";

import { useLocale, useNow, useTranslations } from "next-intl";

import { cn } from "@/lib/cn";
import { fmtRelative, fmtTime } from "@/lib/format";

/**
 * 看板通用 LIVE 状态条:正常显示 LIVE + 数据时间 + "X 前";最近一次刷新失败时
 * 切成「后端离线 · 显示上一帧」(fox 红)。右侧可挂额外 meta(账户、计数等)。
 */
export function LiveStrip({
  asOf,
  isValidating,
  isStaleFrame,
  children,
}: {
  asOf: string;
  isValidating: boolean;
  isStaleFrame: boolean;
  children?: React.ReactNode;
}) {
  const t = useTranslations("status");
  const locale = useLocale();
  const now = useNow({ updateInterval: 10_000 });

  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[11px]">
      {isStaleFrame ? (
        <Meta
          label={t("offline")}
          tone="fox"
          value={t("lastFrame")}
          dot
        />
      ) : (
        <Meta
          label={t("live")}
          tone="bull"
          value={`${fmtTime(asOf, locale)} · ${fmtRelative(asOf, now.getTime(), locale)}`}
          dot
          pulse={isValidating}
        />
      )}
      {children}
    </div>
  );
}

export function Meta({
  label,
  value,
  tone = "muted",
  dot = false,
  pulse = false,
}: {
  label: string;
  value: string;
  tone?: "bull" | "fox" | "muted";
  dot?: boolean;
  pulse?: boolean;
}) {
  const toneText =
    tone === "bull" ? "text-bull" : tone === "fox" ? "text-fox-red" : "text-fg";
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <span className="uppercase tracking-[0.16em] text-fg-muted/60">
        {label}
      </span>
      <span className={cn("flex items-center gap-1.5 tabular-nums", toneText)}>
        {dot && (
          <span className="relative flex size-1.5">
            {pulse && (
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-60" />
            )}
            <span className="relative inline-flex size-1.5 rounded-full bg-current" />
          </span>
        )}
        {value}
      </span>
    </div>
  );
}
