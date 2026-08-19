export type LoginLocale = "en" | "zh";

const RETURN_PATH_ORIGIN = "https://login.inalpha.invalid";

/** 将安全站内返回地址规范化，供语言选择与登录成功跳转共同使用。 */
export function normalizeLoginReturnPath(from: string | null): string | null {
  if (!from?.startsWith("/") || from.startsWith("//") || from.includes("\\")) return null;

  try {
    const url = new URL(from, RETURN_PATH_ORIGIN);
    return url.origin === RETURN_PATH_ORIGIN ? `${url.pathname}${url.search}${url.hash}` : null;
  } catch {
    return null;
  }
}

/**
 * 按返回路径优先选择登录语言；直接访问登录页时才回退到浏览器语言。
 * URL locale 是用户的显式选择，优先级高于浏览器偏好。
 */
export function pickLoginLocale(
  from: string | null,
  browserLanguage: string | undefined,
): LoginLocale {
  const destination = normalizeLoginReturnPath(from);
  if (destination !== null) {
    const pathname = new URL(destination, RETURN_PATH_ORIGIN).pathname;
    return pathname === "/zh" || pathname.startsWith("/zh/") ? "zh" : "en";
  }

  return browserLanguage?.toLowerCase().startsWith("zh") ? "zh" : "en";
}
