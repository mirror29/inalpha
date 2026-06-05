"use client";

import { useTranslations } from "next-intl";
import { RotateCw, WifiOff } from "lucide-react";

/** 整页错误态(连一帧都没拿到)+ 重试。 */
export function ErrorState({
  message,
  onRetry,
}: {
  message?: string;
  onRetry: () => void;
}) {
  const t = useTranslations("common");
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
      <WifiOff className="size-8 text-fox-red" strokeWidth={1.5} />
      <div>
        <div className="font-mono text-sm text-fg">{t("error")}</div>
        {message && (
          <div className="mt-1 font-mono text-xs text-fg-muted">{message}</div>
        )}
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center gap-2 rounded-md border border-border-subtle px-4 py-2 font-mono text-xs text-fg transition-colors hover:border-cyan hover:text-cyan"
      >
        <RotateCw className="size-3.5" />
        {t("retry")}
      </button>
    </div>
  );
}

/** 占位骨架块。 */
export function SkeletonBlock({ className }: { className?: string }) {
  return (
    <div
      className={`animate-pulse rounded-xl border border-border-subtle bg-bg-elev/30 ${className ?? ""}`}
    />
  );
}
