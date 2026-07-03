"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/cn";

/**
 * 侧栏底部账户控件:显示登录用户邮箱 + 登出。
 *
 * 未登录 / 未启用登录(`/api/auth/session` 返 `user: null`)时不渲染 —— 本地 dev 无登录态,
 * 侧栏保持原样。登出后跳 `/login`(站点根路径,不带 locale 前缀,故用 next/navigation)。
 */
export function AccountControl({ collapsed }: { collapsed: boolean }) {
  const t = useTranslations("nav");
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/auth/session")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (alive) setEmail(d?.user?.email ?? null);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  if (!email) return null;

  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
    router.refresh();
  }

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={logout}
        aria-label={t("logout")}
        title={`${email} · ${t("logout")}`}
        className="flex size-8 items-center justify-center rounded-md border border-border-subtle text-fg-muted transition-colors hover:border-cyan/40 hover:bg-bg-elev/60 hover:text-cyan"
      >
        <LogOut className="size-4" strokeWidth={1.75} />
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span
        title={email}
        className="min-w-0 flex-1 truncate font-mono text-[11px] text-fg-muted"
      >
        {email}
      </span>
      <button
        type="button"
        onClick={logout}
        aria-label={t("logout")}
        title={t("logout")}
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-md border border-border-subtle",
          "text-fg-muted transition-colors hover:border-cyan/40 hover:bg-bg-elev/60 hover:text-cyan",
        )}
      >
        <LogOut className="size-3.5" strokeWidth={1.75} />
      </button>
    </div>
  );
}
