"use client";

import { useTranslations } from "next-intl";
import {
  Activity,
  FlaskConical,
  LayoutDashboard,
  Radio,
  ShieldAlert,
  Sigma,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { ThemeToggle } from "./ThemeToggle";

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
  { key: "lab", index: "04", href: "/lab", icon: FlaskConical },
  { key: "factors", index: "05", href: "/factors", icon: Sigma },
  { key: "risk", index: "06", href: "/risk", icon: ShieldAlert },
  // 玄学彩蛋占卜台(纯娱乐)
  { key: "divination", index: "07", href: "/divination", icon: Sparkles },
];

export function ConsoleSidebar() {
  const t = useTranslations("nav");
  const pathname = usePathname();

  return (
    <aside className="sticky top-0 z-10 flex h-dvh w-60 shrink-0 flex-col border-r border-border-subtle bg-bg-deep/70 backdrop-blur-sm">
      {/* Brand —— 朱红印章 + 字标。印章是 Inalpha 的签名标记。 */}
      <div className="flex items-center gap-3 border-b border-border-subtle px-5 py-5">
        <img
          src="/inalpha-seal.png"
          alt="Inalpha"
          width={40}
          height={40}
          className="seal-glow size-10 shrink-0 select-none transition-transform duration-300 hover:rotate-[-4deg] hover:scale-110"
          draggable={false}
        />
        <div className="min-w-0">
          <div className="font-display text-xl leading-none tracking-tight text-fg">
            Inalpha
          </div>
          <div className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-fg-muted">
            {t("console")}
          </div>
        </div>
      </div>

      {/* Nav —— 终端「行号 + 标的」语感:序号 / 图标 / 名称。 */}
      <nav className="flex flex-1 flex-col gap-0.5 p-3">
        {NAV.map((item) => {
          const active = !item.soon && pathname === item.href;
          const Icon = item.icon;
          const inner = (
            <>
              <span
                className={cn(
                  "font-mono text-[10px] tabular-nums transition-colors",
                  active ? "text-cyan" : "text-fg-muted/50",
                )}
              >
                {item.index}
              </span>
              <Icon
                className={cn(
                  "size-4 shrink-0 transition-colors",
                  active
                    ? "text-cyan"
                    : "text-fg-muted group-hover:text-fg",
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
            "group relative flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-[color,background-color,transform] duration-200";

          if (item.soon) {
            return (
              <div
                key={item.key}
                aria-disabled
                className={cn(baseCls, "cursor-not-allowed text-fg-muted/50")}
              >
                {inner}
              </div>
            );
          }

          return (
            <Link
              key={item.key}
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cn(
                baseCls,
                "hover:translate-x-0.5",
                active
                  ? "bg-cyan/10 text-fg"
                  : "text-fg-muted hover:bg-bg-elev/60 hover:text-fg",
              )}
            >
              {/* 激活态:左侧朱红印章刻度(品牌色锚定当前位置)。 */}
              {active && (
                <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r-full bg-seal" />
              )}
              {inner}
            </Link>
          );
        })}
      </nav>

      {/* Footer —— 控制区(主题 / 语言)+ build 标记。 */}
      <div className="flex flex-col gap-3 border-t border-border-subtle px-4 py-3">
        <div className="flex items-center justify-between">
          <ThemeToggle />
          <LocaleSwitcher />
        </div>
        <div className="flex items-center gap-1.5 whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.18em] text-fg-muted/60">
          <span className="size-1.5 shrink-0 rounded-full bg-seal" />
          <span>Build · D-11</span>
        </div>
      </div>
    </aside>
  );
}
