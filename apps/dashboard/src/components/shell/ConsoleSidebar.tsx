"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  Activity,
  FlaskConical,
  LayoutDashboard,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Radio,
  ShieldAlert,
  Sigma,
  Sparkles,
  X,
  type LucideIcon,
} from "lucide-react";

import { Link, usePathname } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { LocaleSwitcher } from "./LocaleSwitcher";
import { ThemeToggle } from "./ThemeToggle";

interface NavItem {
  key: string;
  href: string;
  icon: LucideIcon;
  /** false = 已落地;true = 占位(灰 + soon 角标)。 */
  soon?: boolean;
}

// 顺序按「看板 → 研究 → 执行 → 风控 → 彩蛋 → 日志」的操作动线:
// 总览(总控制台)→ 策略实验室 → Live Runner → 因子库 → 风控 → 狐神签 → Agent 活动。
const NAV: NavItem[] = [
  { key: "overview", href: "/", icon: LayoutDashboard },
  { key: "lab", href: "/lab", icon: FlaskConical },
  { key: "runners", href: "/runners", icon: Radio },
  { key: "factors", href: "/factors", icon: Sigma },
  { key: "risk", href: "/risk", icon: ShieldAlert },
  // 玄学彩蛋占卜台(纯娱乐)
  { key: "divination", href: "/divination", icon: Sparkles },
  { key: "activity", href: "/activity", icon: Activity },
];

const LS_COLLAPSED = "inalpha-sidebar-collapsed";

/**
 * 控制台导航 —— 三态自适应:
 *  - 桌面(lg+):常驻左栏,可折叠成「仅图标」窄轨(w-16,持久化)。
 *  - 移动(<lg):左栏隐藏,顶栏汉堡唤出滑入抽屉 + 半透遮罩;路由切换 / Esc / 点遮罩即关。
 *
 * 桌面栏与移动抽屉共用 {@link SidebarBody},避免导航重复定义。
 */
export function ConsoleSidebar() {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // 桌面折叠态持久化(mount 后读,避免 SSR/CSR 首帧不一致)。
  useEffect(() => {
    setCollapsed(localStorage.getItem(LS_COLLAPSED) === "1");
  }, []);

  const toggleCollapsed = () =>
    setCollapsed((c) => {
      const next = !c;
      localStorage.setItem(LS_COLLAPSED, next ? "1" : "0");
      return next;
    });

  // 路由切换关移动抽屉(切面即收起,避免遮挡新内容)。
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  // 抽屉打开时:Esc 关 + 锁背景滚动(防遮罩下方页面仍可拖动)。
  useEffect(() => {
    if (!mobileOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMobileOpen(false);
    };
    window.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [mobileOpen]);

  return (
    <>
      {/* 移动顶栏(lg 隐藏)—— 汉堡 + 字标。 */}
      <header className="fixed inset-x-0 top-0 z-30 flex h-14 items-center gap-3 border-b border-border-subtle bg-bg-deep/85 px-4 backdrop-blur-md lg:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          aria-label={t("menu")}
          className="-ml-1 flex size-9 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-elev/60 hover:text-fg"
        >
          <Menu className="size-5" strokeWidth={1.75} />
        </button>
        <img
          src="/inalpha-seal.png"
          alt=""
          width={28}
          height={28}
          className="seal-glow size-7 shrink-0 select-none"
          draggable={false}
        />
        <span className="font-display text-lg leading-none tracking-tight text-fg">
          Inalpha
        </span>
      </header>

      {/* 桌面常驻栏(in-flow,可折叠)。
          padding-bottom 让出底部活动日志条高度(--activity-h,与 main 同源)—— 否则
          fixed 的 ActivityFooter(z-20)会盖住本栏 z-10 的底部控制(折叠后的展开钮就被压住打不开)。 */}
      <aside
        style={{ paddingBottom: "var(--activity-h, 0px)" }}
        className={cn(
          "sticky top-0 z-10 hidden h-dvh shrink-0 flex-col border-r border-border-subtle bg-bg-deep/70 backdrop-blur-sm transition-[width] duration-200 lg:flex",
          collapsed ? "w-16" : "w-60",
        )}
      >
        <SidebarBody
          t={t}
          pathname={pathname}
          collapsed={collapsed}
          onToggleCollapsed={toggleCollapsed}
        />
      </aside>

      {/* 移动抽屉 + 遮罩(lg 隐藏)。 */}
      <div
        aria-hidden
        onClick={() => setMobileOpen(false)}
        className={cn(
          "fixed inset-0 z-40 bg-bg-deep/60 backdrop-blur-sm transition-opacity duration-200 lg:hidden",
          mobileOpen ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      />
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-64 max-w-[80vw] flex-col border-r border-border-subtle bg-bg-deep/95 backdrop-blur-md transition-transform duration-200 lg:hidden motion-reduce:transition-none",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <SidebarBody
          t={t}
          pathname={pathname}
          collapsed={false}
          onNavigate={() => setMobileOpen(false)}
          onMobileClose={() => setMobileOpen(false)}
        />
      </aside>
    </>
  );
}

/** 栏体(品牌 + 导航 + 控制区)—— 桌面栏与移动抽屉共用。 */
function SidebarBody({
  t,
  pathname,
  collapsed,
  onToggleCollapsed,
  onNavigate,
  onMobileClose,
}: {
  t: ReturnType<typeof useTranslations>;
  pathname: string;
  collapsed: boolean;
  onToggleCollapsed?: () => void;
  onNavigate?: () => void;
  onMobileClose?: () => void;
}) {
  return (
    <>
      {/* Brand —— 朱红印章 + 字标。折叠态仅留印章。 */}
      <div
        className={cn(
          "flex items-center gap-3 border-b border-border-subtle py-5",
          collapsed ? "justify-center px-0" : "px-5",
        )}
      >
        {collapsed && onToggleCollapsed ? (
          // 折叠态:印章本身即「展开」按钮(悬浮露出展开图标),配底部按钮双保险。
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label={t("expand")}
            title={t("expand")}
            className="group relative flex size-10 shrink-0 items-center justify-center rounded-md"
          >
            <img
              src="/inalpha-seal.png"
              alt="Inalpha"
              width={40}
              height={40}
              className="seal-glow size-10 select-none transition-opacity duration-200 group-hover:opacity-20"
              draggable={false}
            />
            <PanelLeftOpen
              className="absolute size-5 text-cyan opacity-0 transition-opacity duration-200 group-hover:opacity-100"
              strokeWidth={2}
            />
          </button>
        ) : (
          <img
            src="/inalpha-seal.png"
            alt="Inalpha"
            width={40}
            height={40}
            className="seal-glow size-10 shrink-0 select-none transition-transform duration-300 hover:rotate-[-4deg] hover:scale-110"
            draggable={false}
          />
        )}
        {!collapsed && (
          <div className="min-w-0 flex-1">
            <div className="font-display text-xl leading-none tracking-tight text-fg">
              Inalpha
            </div>
            <div className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-fg-muted">
              {t("console")}
            </div>
          </div>
        )}
        {onMobileClose ? (
          <button
            type="button"
            onClick={onMobileClose}
            aria-label={t("close")}
            className="ml-auto flex size-8 items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-bg-elev/60 hover:text-fg"
          >
            <X className="size-4" strokeWidth={1.75} />
          </button>
        ) : onToggleCollapsed && !collapsed ? (
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label={t("collapse")}
            title={t("collapse")}
            className="ml-auto flex size-8 shrink-0 items-center justify-center rounded-md border border-border-subtle text-fg-muted transition-colors hover:border-cyan/40 hover:bg-bg-elev/60 hover:text-cyan"
          >
            <PanelLeftClose className="size-4" strokeWidth={1.75} />
          </button>
        ) : null}
      </div>

      {/* Nav —— 折叠态仅图标(居中 + title 悬浮提示)。 */}
      <nav className="flex flex-1 flex-col gap-0.5 p-3">
        {NAV.map((item) => {
          const active = !item.soon && pathname === item.href;
          const Icon = item.icon;
          const inner = (
            <>
              <Icon
                className={cn(
                  "size-4 shrink-0 transition-colors",
                  active ? "text-cyan" : "text-fg-muted group-hover:text-fg",
                )}
                strokeWidth={1.75}
              />
              {!collapsed && (
                <>
                  <span className="flex-1 truncate">{t(item.key)}</span>
                  {item.soon && (
                    <span className="rounded-sm border border-border-subtle px-1 py-px font-mono text-[9px] uppercase tracking-wider text-fg-muted/70">
                      {t("soon")}
                    </span>
                  )}
                </>
              )}
            </>
          );

          const baseCls = cn(
            "group relative flex items-center rounded-md py-2 text-sm transition-[color,background-color,transform] duration-200",
            collapsed ? "justify-center px-0" : "gap-2.5 px-3",
          );

          if (item.soon) {
            return (
              <div
                key={item.key}
                aria-disabled
                title={collapsed ? t(item.key) : undefined}
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
              onClick={onNavigate}
              aria-current={active ? "page" : undefined}
              title={collapsed ? t(item.key) : undefined}
              className={cn(
                baseCls,
                !collapsed && "hover:translate-x-0.5",
                active
                  ? "bg-cyan/10 text-fg"
                  : "text-fg-muted hover:bg-bg-elev/60 hover:text-fg",
              )}
            >
              {/* 激活态:左侧朱红印章刻度(品牌色锚定当前位置)。 */}
              {active && !collapsed && (
                <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r-full bg-seal" />
              )}
              {inner}
            </Link>
          );
        })}
      </nav>

      {/* Footer —— 控制区(主题 / 语言)+ 折叠开关 + build 标记。 */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-3 border-t border-border-subtle px-2 py-3">
          <ThemeToggle />
          {onToggleCollapsed && (
            <button
              type="button"
              onClick={onToggleCollapsed}
              aria-label={t("expand")}
              title={t("expand")}
              className="flex size-8 items-center justify-center rounded-md border border-border-subtle text-fg-muted transition-colors hover:border-cyan/40 hover:bg-bg-elev/60 hover:text-cyan"
            >
              <PanelLeftOpen className="size-4" strokeWidth={1.75} />
            </button>
          )}
        </div>
      ) : (
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
      )}
    </>
  );
}
