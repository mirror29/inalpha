import { NextIntlClientProvider } from "next-intl";
import { setRequestLocale } from "next-intl/server";

import enMessages from "../../../messages/en.json";
import { DocumentShell } from "../_shared/DocumentShell";

export default function DefaultLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  setRequestLocale("en");

  return (
    <DocumentShell lang="en">
      <NextIntlClientProvider locale="en" messages={enMessages}>
        {children}
      </NextIntlClientProvider>
    </DocumentShell>
  );
}
