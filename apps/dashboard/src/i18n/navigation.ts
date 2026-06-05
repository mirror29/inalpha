import { createNavigation } from "next-intl/navigation";
import { routing } from "./routing";

/**
 * locale 感知的 Link / useRouter / usePathname —— 看板内部导航统一走这套,
 * 自动带上当前 locale 前缀。
 */
export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation(routing);
