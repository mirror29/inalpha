"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { LogOut, Settings } from "lucide-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/cn";

/** 配置与账户共用紧凑菜单；无登录态时仍保留配置入口。 */
export function AccountControl({
  collapsed,
  children,
}: {
  collapsed: boolean;
  children: ReactNode;
}) {
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

  /** 结束当前会话后返回站点登录入口。 */
  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => {});
    router.replace("/login");
    router.refresh();
  }

  return (
    <div
      className={cn("flex items-center gap-2", collapsed && "justify-center")}
    >
      {!collapsed && (
        <span
          title={email ?? undefined}
          className="min-w-0 flex-1 truncate font-mono text-[11px] text-fg-muted"
        >
          {email ?? t("config")}
        </span>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="icon"
            className="size-8 shrink-0 text-fg-muted"
            aria-label={t("config")}
            title={t("config")}
          >
            <Settings className="size-4" strokeWidth={1.75} />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align={collapsed ? "center" : "end"}
          className="w-52"
        >
          {children}
          {email && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={logout}>
                <LogOut className="size-4" strokeWidth={1.75} />
                {t("logout")}
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
