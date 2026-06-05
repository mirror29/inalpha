"use client";

import { useLocale } from "next-intl";

import { usePathname, useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

const LOCALES = [
  { code: "en", label: "EN" },
  { code: "zh", label: "中" },
] as const;

/**
 * en / zh 切换 —— 保持当前路径,只换 locale 前缀(走 next-intl navigation)。
 */
export function LocaleSwitcher() {
  const locale = useLocale();
  const pathname = usePathname();
  const router = useRouter();

  return (
    <div className="inline-flex items-center gap-px rounded-md border border-border-subtle bg-bg-elev/40 p-0.5">
      {LOCALES.map((l) => {
        const active = l.code === locale;
        return (
          <button
            key={l.code}
            type="button"
            aria-pressed={active}
            onClick={() => router.replace(pathname, { locale: l.code })}
            className={cn(
              "rounded px-2 py-1 font-mono text-xs transition-colors",
              active
                ? "bg-cyan/15 text-cyan"
                : "text-fg-muted hover:text-fg",
            )}
          >
            {l.label}
          </button>
        );
      })}
    </div>
  );
}
