"use client";

import { useTranslations } from "next-intl";
import {
  Activity,
  FlaskConical,
  LayoutDashboard,
  Radio,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";

import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { LocaleSwitcher } from "./LocaleSwitcher";

interface NavItem {
  key: string;
  index: string;
  href: string;
  icon: LucideIcon;
  /** false = 已落地;true = 占位(灰 + soon 角标)。 */
  soon?: boolean;
}

const NAV: NavItem[] = [
  { key: "overview", index: "01", href: "/", icon: LayoutDashboard },
  { key: "runners", index: "02", href: "/runners", icon: Radio },
  { key: "activity", index: "03", href: "/activity", icon: Activity },
  { key: "lab", index: "04", href: "/lab", icon: FlaskConical, soon: true },
  { key: "risk", index: "05", href: "/risk", icon: ShieldAlert, soon: true },
];

export function ConsoleSidebar() {
  const t = useTranslations("nav");
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 flex h-dvh w-60 shrink-0 flex-col border-r border-border-subtle bg-bg-deep/60">
      {/* Wordmark */}
      <div className="border-b border-border-subtle px-5 py-5">
        <div className="font-display text-xl tracking-tight text-fg">
          Inalpha
        </div>
        <div className="mt-1 font-mono text-[10px] uppercase tracking-[0.2em] text-fg-muted">
          {t("console")}
        </div>
      </div>

      {/* Nav */}
      <nav className="flex flex-1 flex-col gap-0.5 p-3">
        {NAV.map((item) => {
          const active = !item.soon && pathname === item.href;
          const Icon = item.icon;
          const inner = (
            <>
              <span className="font-mono text-[10px] text-fg-muted/60 tabular-nums">
                {item.index}
              </span>
              <Icon
                className={cn(
                  "size-4 shrink-0",
                  active ? "text-cyan" : "text-fg-muted",
                )}
                strokeWidth={1.75}
              />
              <span className="flex-1 truncate">{t(item.key)}</span>
              {item.soon && (
                <span className="rounded-sm border border-border-subtle px-1 py-px font-mono text-[9px] uppercase tracking-wider text-fg-muted/70">
                  {t("soon")}
                </span>
              )}
            </>
          );

          const baseCls =
            "group flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors";

          if (item.soon) {
            return (
              <div
                key={item.key}
                aria-disabled
                className={cn(
                  baseCls,
                  "cursor-not-allowed text-fg-muted/50",
                )}
              >
                {inner}
              </div>
            );
          }

          return (
            <Link
              key={item.key}
              href={item.href}
              className={cn(
                baseCls,
                active
                  ? "bg-cyan/10 text-fg"
                  : "text-fg-muted hover:bg-bg-elev/50 hover:text-fg",
              )}
            >
              {inner}
              {active && (
                <span className="absolute left-0 h-5 w-0.5 -translate-x-3 rounded-full bg-cyan" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="flex items-center justify-between border-t border-border-subtle px-4 py-3">
        <span className="font-mono text-[10px] uppercase tracking-wider text-fg-muted/60">
          D-11
        </span>
        <LocaleSwitcher />
      </div>
    </aside>
  );
}
