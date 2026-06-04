import { NextIntlClientProvider, hasLocale } from "next-intl";
import { getMessages, setRequestLocale } from "next-intl/server";
import { notFound } from "next/navigation";

import { ConsoleSidebar } from "@/components/shell/ConsoleSidebar";
import { routing } from "@/i18n/routing";

export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

/**
 * Locale 外壳 —— 左侧固定导航 + 右侧看板内容。
 * 背景叠 drafting-table 网格 + 纸张颗粒,与官网工程图纸调性统一。
 */
export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) {
    notFound();
  }
  setRequestLocale(locale);
  const messages = await getMessages();

  return (
    <NextIntlClientProvider messages={messages}>
      <div className="grain flex min-h-dvh">
        <ConsoleSidebar />
        <main className="vignette relative flex-1 overflow-x-hidden">
          <div className="relative z-10 mx-auto max-w-[1400px] px-6 py-8 lg:px-10">
            {children}
          </div>
        </main>
      </div>
    </NextIntlClientProvider>
  );
}
