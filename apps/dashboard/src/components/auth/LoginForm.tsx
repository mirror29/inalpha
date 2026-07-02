"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

/**
 * 登录表单。登录页在 `[locale]` 外壳之外(不套控制台侧栏 / 对话栏 / intl provider),
 * 故文案在此按 `navigator.language` 做最小中英切换,不依赖 next-intl。
 */

const STRINGS = {
  en: {
    title: "Operator Console",
    subtitle: "Sign in to continue",
    email: "Email",
    password: "Password",
    submit: "Sign in",
    submitting: "Signing in…",
    invalid: "Incorrect email or password",
    rateLimited: "Too many attempts, try again later",
    unavailable: "Login service unavailable, try again later",
  },
  zh: {
    title: "操作控制台",
    subtitle: "登录以继续",
    email: "邮箱",
    password: "密码",
    submit: "登录",
    submitting: "登录中…",
    invalid: "邮箱或密码不正确",
    rateLimited: "尝试过于频繁,请稍后再试",
    unavailable: "登录服务暂不可用,请稍后重试",
  },
};

function pickLang(): "en" | "zh" {
  if (typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("zh")) {
    return "zh";
  }
  return "en";
}

export function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const t = STRINGS[pickLang()];

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (res.ok) {
        const from = params.get("from");
        // 只接受站内相对路径,防开放重定向。
        const dest = from && from.startsWith("/") && !from.startsWith("//") ? from : "/";
        router.replace(dest);
        router.refresh();
        return;
      }
      setError(
        res.status === 401
          ? t.invalid
          : res.status === 429
            ? t.rateLimited
            : t.unavailable,
      );
    } catch {
      setError(t.unavailable);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="w-full max-w-sm rounded-xl border border-border-subtle bg-bg-elev/70 p-8 shadow-lg backdrop-blur-md"
    >
      <div className="flex flex-col items-center gap-3">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/inalpha-seal.png"
          alt="Inalpha"
          width={56}
          height={56}
          className="seal-glow size-14 select-none"
          draggable={false}
        />
        <div className="text-center">
          <div className="font-display text-2xl leading-none tracking-tight text-fg">Inalpha</div>
          <div className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.2em] text-fg-muted">
            {t.title}
          </div>
        </div>
      </div>

      <p className="mt-6 text-center text-sm text-fg-muted">{t.subtitle}</p>

      <div className="mt-6 flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[11px] uppercase tracking-wider text-fg-muted">
            {t.email}
          </span>
          <input
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-md border border-border-subtle bg-bg-deep/60 px-3 py-2 text-sm text-fg outline-none transition-colors focus:border-cyan/50"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="font-mono text-[11px] uppercase tracking-wider text-fg-muted">
            {t.password}
          </span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded-md border border-border-subtle bg-bg-deep/60 px-3 py-2 text-sm text-fg outline-none transition-colors focus:border-cyan/50"
          />
        </label>
      </div>

      {error && (
        <p role="alert" className="mt-4 text-center text-sm text-red-400">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={loading}
        className="mt-6 w-full rounded-md bg-cyan/15 py-2.5 text-sm font-medium text-cyan transition-colors hover:bg-cyan/25 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {loading ? t.submitting : t.submit}
      </button>
    </form>
  );
}
