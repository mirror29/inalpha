import { NextIntlClientProvider } from "next-intl";
import { getMessages, setRequestLocale } from "next-intl/server";
import { notFound } from "next/navigation";

import { DocumentShell } from "../_shared/DocumentShell";

export const dynamicParams = false;

export function generateStaticParams() {
  return [{ locale: "zh" }];
}

export default async function LocaleLayout({
  children,
  params,
}: Readonly<{
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}>) {
  const { locale } = await params;
  if (locale !== "zh") {
    notFound();
  }

  setRequestLocale(locale);
  const messages = await getMessages();

  return (
    <DocumentShell lang="zh">
      <NextIntlClientProvider locale={locale} messages={messages}>
        {children}
      </NextIntlClientProvider>
    </DocumentShell>
  );
}
