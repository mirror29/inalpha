import type { Metadata } from "next";
import { setRequestLocale } from "next-intl/server";
import zhMessages from "../../../messages/zh.json";
import { notFound } from "next/navigation";

import HomePage from "../_shared/HomePage";
import { buildHomeMetadata } from "@/lib/seo";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  if (locale !== "zh") {
    return {};
  }

  return buildHomeMetadata(
    "zh",
    zhMessages.meta.title,
    zhMessages.meta.description,
  );
}

export default async function ChineseHomePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (locale !== "zh") {
    notFound();
  }

  return <HomePage locale="zh" />;
}
