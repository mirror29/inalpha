"use client";

import { useTranslations } from "next-intl";
import {
  Activity,
  ArrowLeftRight,
  Clock,
  ShieldAlert,
  ShieldQuestion,
  type LucideIcon,
} from "lucide-react";

import type { ActivityKind } from "@/lib/types";
import { cn } from "@/lib/cn";

const META: Record<ActivityKind, { icon: LucideIcon; cls: string }> = {
  scheduler: { icon: Clock, cls: "border-cyan/30 bg-cyan/10 text-cyan" },
  permission: { icon: ShieldQuestion, cls: "border-gold/30 bg-gold/10 text-gold" },
  decision: { icon: Activity, cls: "border-bull/30 bg-bull/10 text-bull" },
  risk: { icon: ShieldAlert, cls: "border-fox-red/30 bg-fox-red/10 text-fox-red" },
  order: { icon: ArrowLeftRight, cls: "border-border-subtle bg-bg-elev/40 text-fg-muted" },
};

/** 活动事件的模块归属标签(图标 + 配色 + i18n 文案)。 */
export function KindTag({ kind }: { kind: ActivityKind }) {
  const t = useTranslations("activity.filter");
  const { icon: Icon, cls } = META[kind];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        cls,
      )}
    >
      <Icon className="size-3" strokeWidth={2} />
      {t(kind)}
    </span>
  );
}

export function kindIcon(kind: ActivityKind): LucideIcon {
  return META[kind].icon;
}
