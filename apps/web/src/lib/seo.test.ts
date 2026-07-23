import { describe, expect, it } from "vitest";

import {
  buildHomeMetadata,
  buildHomeStructuredData,
  getCanonicalUrl,
  getFaqItems,
  isSupportedLocale,
} from "./seo";

describe("SEO helpers", () => {
  it("recognizes only supported locales", () => {
    expect(isSupportedLocale("en")).toBe(true);
    expect(isSupportedLocale("zh")).toBe(true);
    expect(isSupportedLocale("fr")).toBe(false);
  });

  it("uses the canonical URL for each homepage locale", () => {
    expect(getCanonicalUrl("en")).toBe("https://inalpha.dev/");
    expect(getCanonicalUrl("zh")).toBe("https://inalpha.dev/zh/");
  });

  it("generates reciprocal homepage alternates", () => {
    const metadata = buildHomeMetadata("zh", "中文标题", "中文描述");

    expect(metadata.alternates).toMatchObject({
      canonical: "https://inalpha.dev/zh/",
      languages: {
        en: "https://inalpha.dev/",
        zh: "https://inalpha.dev/zh/",
        "x-default": "https://inalpha.dev/",
      },
    });
    expect(metadata.openGraph).toMatchObject({
      url: "https://inalpha.dev/zh/",
      locale: "zh",
    });
  });

  it("keeps metadata fields consistent for both locales", () => {
    for (const locale of ["en", "zh"] as const) {
      const metadata = buildHomeMetadata(locale, `${locale} title`, `${locale} description`);

      expect(metadata).toMatchObject({
        title: `${locale} title`,
        description: `${locale} description`,
        robots: { index: true, follow: true },
        twitter: { title: `${locale} title`, description: `${locale} description` },
        openGraph: {
          title: `${locale} title`,
          description: `${locale} description`,
          url: getCanonicalUrl(locale),
          locale,
        },
      });
    }
  });

  it("derives localized FAQ schema from visible translation items", () => {
    for (const locale of ["en", "zh"] as const) {
      const schema = buildHomeStructuredData(locale);
      const types = schema["@graph"].map((item) => item["@type"]);
      const faq = schema["@graph"].find((item) => item["@type"] === "FAQPage");

      expect(types).toEqual([
        "Organization",
        "WebSite",
        "SoftwareSourceCode",
        "FAQPage",
      ]);
      expect(schema["@graph"][0]).toMatchObject({
        "@id": "https://inalpha.dev/#organization",
      });
      expect(faq).toMatchObject({
        "@id": `${getCanonicalUrl(locale)}#faq`,
        inLanguage: locale,
        mainEntity: getFaqItems(locale).map((item) => ({
          "@type": "Question",
          name: item.question,
          acceptedAnswer: { "@type": "Answer", text: item.answer },
        })),
      });
    }
  });

  it("does not advertise an unavailable site search", () => {
    expect(JSON.stringify(buildHomeStructuredData("zh"))).not.toContain(
      "SearchAction",
    );
  });
});
