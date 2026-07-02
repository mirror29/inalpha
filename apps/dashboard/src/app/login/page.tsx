import { Suspense } from "react";

import { LoginForm } from "@/components/auth/LoginForm";

/**
 * 登录页。刻意放在 `[locale]` 外壳之外 —— 不套控制台侧栏 / 对话栏 / 活动日志,
 * 避免未登录时这些组件挂载后打 401。middleware 未登录时重定向到这里。
 */
export const metadata = {
  title: "Sign in · Inalpha",
  robots: { index: false, follow: false },
};

export default function LoginPage() {
  return (
    <main className="grain flex min-h-dvh items-center justify-center px-4">
      {/* useSearchParams 需要 Suspense 边界。 */}
      <Suspense>
        <LoginForm />
      </Suspense>
    </main>
  );
}
