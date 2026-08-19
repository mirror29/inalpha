import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { LoginForm } from "./LoginForm";
import { pickLoginLocale } from "./login-locale";

const navigation = vi.hoisted(() => ({ from: "/zh" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: (key: string) => (key === "from" ? navigation.from : null) }),
}));

describe("pickLoginLocale", () => {
  it.each([
    ["/zh", "en-US", "zh"],
    ["/zh/runners", "en-US", "zh"],
    ["/zh?tab=runners", "en-US", "zh"],
    ["/zh#positions", "en-US", "zh"],
    ["/zh/../en", "zh-CN", "en"],
    ["/zh/%2e%2e/en", "zh-CN", "en"],
    ["/en/../zh", "en-US", "zh"],
    ["/", "zh-CN", "en"],
    ["/runners", "zh-CN", "en"],
    ["/zh-CN", "zh-CN", "en"],
    ["/zh-malicious", "zh-CN", "en"],
  ] as const)("maps from=%s with browser=%s to %s", (from, browserLanguage, expected) => {
    expect(pickLoginLocale(from, browserLanguage)).toBe(expected);
  });

  it("falls back to the browser language without a safe return path", () => {
    expect(pickLoginLocale(null, "zh-CN")).toBe("zh");
    expect(pickLoginLocale(null, "en-US")).toBe("en");
    expect(pickLoginLocale("//evil.example", "zh-CN")).toBe("zh");
    expect(pickLoginLocale("/\\evil.example", "zh-CN")).toBe("zh");
    expect(pickLoginLocale("https://evil.example", "en-US")).toBe("en");
    expect(pickLoginLocale("zh/runners", "zh-CN")).toBe("zh");
    expect(pickLoginLocale(null, undefined)).toBe("en");
  });
});

describe("LoginForm", () => {
  it("renders Chinese copy when the return path starts at /zh", () => {
    navigation.from = "/zh";

    const html = renderToStaticMarkup(createElement(LoginForm));

    expect(html).toContain("操作控制台");
    expect(html).toContain("登录以继续");
    expect(html).toContain("邮箱");
    expect(html).toContain("密码");
    expect(html).not.toContain("Sign in to continue");
  });
});
