import type { MetadataRoute } from "next";

import { SITE_URL } from "@/lib/seo";

export const dynamic = "force-static";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: `${SITE_URL}/`,
      alternates: {
        languages: {
          en: `${SITE_URL}/`,
          zh: `${SITE_URL}/zh/`,
          "x-default": `${SITE_URL}/`,
        },
      },
    },
    {
      url: `${SITE_URL}/zh/`,
      alternates: {
        languages: {
          en: `${SITE_URL}/`,
          zh: `${SITE_URL}/zh/`,
          "x-default": `${SITE_URL}/`,
        },
      },
    },
    { url: `${SITE_URL}/privacy/` },
    { url: `${SITE_URL}/terms/` },
  ];
}
