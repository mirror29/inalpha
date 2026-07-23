import type { Metadata } from "next";

import enMessages from "../../../messages/en.json";

import HomePage from "../_shared/HomePage";
import { buildHomeMetadata } from "@/lib/seo";

export function generateMetadata(): Metadata {
  return buildHomeMetadata("en", enMessages.meta.title, enMessages.meta.description);
}

export default function EnglishHomePage() {
  return <HomePage locale="en" />;
}
