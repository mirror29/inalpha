"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLocale } from "next-intl";

import { routing, type Locale } from "@/i18n/routing";
import { cn } from "@/lib/cn";

const labels: Record<Locale, string> = {
  en: "EN",
  zh: "中",
};

export function LocaleSwitcher() {
  const currentLocale = useLocale() as Locale;
  const pathname = usePathname();

  function pathForLocale(target: Locale) {
    // pathname like "/" or "/zh" or "/zh/anything"
    const segments = pathname.split("/").filter(Boolean);
    const head = segments[0];
    const rest =
      head && (routing.locales as readonly string[]).includes(head)
        ? segments.slice(1)
        : segments;
    const tail = rest.length > 0 ? "/" + rest.join("/") : "";
    if (target === routing.defaultLocale) return tail || "/";
    return `/${target}${tail}`;
  }

  return (
    <nav className="flex items-center gap-1 rounded-full border border-border-subtle bg-bg-elev/40 p-1 text-xs font-mono backdrop-blur">
      {routing.locales.map((locale) => {
        const active = locale === currentLocale;
        return (
          <Link
            key={locale}
            href={pathForLocale(locale)}
            className={cn(
              "rounded-full px-2.5 py-1 transition-colors",
              active
                ? "bg-cyan/15 text-cyan"
                : "text-fg-muted hover:text-fg",
            )}
            aria-current={active ? "page" : undefined}
          >
            {labels[locale]}
          </Link>
        );
      })}
    </nav>
  );
}
