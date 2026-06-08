import { NextIntlClientProvider, hasLocale } from "next-intl";
import { getMessages, setRequestLocale } from "next-intl/server";
import { notFound } from "next/navigation";

import { ActivityFooter } from "@/components/activity/ActivityFooter";
import { ConsoleChat } from "@/components/chat/ConsoleChat";
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
          {/* 移动端顶栏 fixed h-14,内容上方留位(lg 无顶栏,回正常间距)。 */}
          <div className="relative z-10 mx-auto max-w-[1400px] px-4 pb-8 pt-[4.5rem] sm:px-6 lg:px-10 lg:py-8">
            {children}
          </div>
        </main>
        {/* 内嵌 agent 对话栏 —— 常驻 layout,切面切换不丢对话(见 ConsoleChat)。 */}
        <ConsoleChat />
        {/* 常驻底部活动日志(终端风)—— 随时可回溯 agent 跨模块活动(见 ActivityFooter)。 */}
        <ActivityFooter />
      </div>
    </NextIntlClientProvider>
  );
}
