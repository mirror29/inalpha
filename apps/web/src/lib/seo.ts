import type { Metadata } from "next";

import enMessages from "../../messages/en.json";
import zhMessages from "../../messages/zh.json";

import { SCHEMA_COPY } from "./seo-schema-copy";

export const SITE_URL = "https://inalpha.dev";

export type SupportedLocale = "en" | "zh";

type FaqItem = {
  question: string;
  answer: string;
};

const MESSAGES = {
  en: enMessages,
  zh: zhMessages,
} as const;

export function isSupportedLocale(locale: string): locale is SupportedLocale {
  return locale === "en" || locale === "zh";
}

export function getCanonicalUrl(locale: SupportedLocale): string {
  return `${SITE_URL}${locale === "en" ? "" : "/zh"}/`;
}

export function getHomeAlternates() {
  return {
    languages: {
      en: `${SITE_URL}/`,
      zh: `${SITE_URL}/zh/`,
      "x-default": `${SITE_URL}/`,
    },
  };
}

export function buildHomeMetadata(
  locale: SupportedLocale,
  title: string,
  description: string,
): Metadata {
  const canonical = getCanonicalUrl(locale);

  return {
    title,
    description,
    metadataBase: new URL(SITE_URL),
    alternates: {
      canonical,
      ...getHomeAlternates(),
    },
    openGraph: {
      title,
      description,
      url: canonical,
      siteName: "Inalpha",
      images: [{ url: "/og.png", width: 1200, height: 630, alt: title }],
      type: "website",
      locale,
    },
    twitter: {
      card: "summary_large_image",
      title,
      description,
      images: ["/og.png"],
    },
    robots: {
      index: true,
      follow: true,
      "max-snippet": -1,
      "max-image-preview": "large",
    },
  };
}

export function getFaqItems(locale: SupportedLocale): FaqItem[] {
  return MESSAGES[locale].faq.items;
}

export function buildHomeStructuredData(locale: SupportedLocale) {
  const canonical = getCanonicalUrl(locale);
  const copy = SCHEMA_COPY[locale];

  return {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Organization",
        "@id": `${SITE_URL}/#organization`,
        name: "Inalpha",
        url: SITE_URL,
        description: copy.organizationDescription,
        sameAs: ["https://github.com/mirror29/inalpha", "https://inalpha.substack.com"],
      },
      {
        "@type": "WebSite",
        "@id": `${SITE_URL}/#website`,
        name: "Inalpha",
        url: SITE_URL,
        description: copy.websiteDescription,
        inLanguage: locale,
        publisher: { "@id": `${SITE_URL}/#organization` },
      },
      {
        "@type": "SoftwareSourceCode",
        "@id": `${canonical}#source-code`,
        name: "Inalpha",
        description: copy.sourceDescription,
        codeRepository: "https://github.com/mirror29/inalpha",
        programmingLanguage: ["TypeScript", "Python"],
        license: "https://www.gnu.org/licenses/agpl-3.0.en.html",
        applicationCategory: "Finance",
        author: { "@type": "Person", name: "Miro" },
        inLanguage: locale,
      },
      {
        "@type": "FAQPage",
        "@id": `${canonical}#faq`,
        inLanguage: locale,
        mainEntity: getFaqItems(locale).map((item) => ({
          "@type": "Question",
          name: item.question,
          acceptedAnswer: { "@type": "Answer", text: item.answer },
        })),
      },
    ],
  };
}
