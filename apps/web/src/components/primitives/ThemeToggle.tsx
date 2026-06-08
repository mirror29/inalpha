"use client";

import * as React from "react";
import { useTranslations } from "next-intl";
import { Moon, Sun } from "lucide-react";

import { cn } from "@/lib/cn";

type Theme = "dark" | "light";

const STORAGE_KEY = "inalpha-theme";

/** 把主题写到 <html data-theme> 并落 localStorage。 */
function applyTheme(theme: Theme) {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* 隐私模式下 localStorage 不可写 —— 忽略，主题仍在本次会话生效。 */
  }
}

/**
 * 黑白双主题切换 —— Dark ⇄ Light。
 * 与 LocaleSwitcher 同款胶囊样式；实际主题由 <html data-theme> 驱动，
 * 防闪烁脚本（root layout）在首帧前已按 localStorage / 系统偏好设好，
 * 这里只负责挂载后读当前值 + 用户点击切换。
 */
export function ThemeToggle() {
  const t = useTranslations("theme");
  const [theme, setTheme] = React.useState<Theme>("dark");

  // 挂载后同步真实 DOM 状态（SSR 默认 dark，避免 hydration 不一致）。
  React.useEffect(() => {
    const current =
      (document.documentElement.getAttribute("data-theme") as Theme) ?? "dark";
    setTheme(current);
  }, []);

  const choose = (next: Theme) => {
    setTheme(next);
    applyTheme(next);
  };

  const options: { value: Theme; icon: typeof Sun; label: string }[] = [
    { value: "dark", icon: Moon, label: t("dark") },
    { value: "light", icon: Sun, label: t("light") },
  ];

  return (
    <div
      className="flex items-center gap-1 rounded-full border border-border-subtle bg-bg-elev/40 p-1 backdrop-blur"
      role="group"
      aria-label={t("label")}
    >
      {options.map(({ value, icon: Icon, label }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            aria-pressed={active}
            title={label}
            onClick={() => choose(value)}
            className={cn(
              "flex items-center justify-center rounded-full p-1.5 transition-colors",
              active ? "bg-cyan/15 text-cyan" : "text-fg-muted hover:text-fg",
            )}
          >
            <Icon className="size-3.5" strokeWidth={2} />
          </button>
        );
      })}
    </div>
  );
}
