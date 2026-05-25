import { setRequestLocale } from "next-intl/server";

import { CTAFooter } from "@/components/sections/CTAFooter";
import { EngineeringDiscipline } from "@/components/sections/EngineeringDiscipline";
import { Hero } from "@/components/sections/Hero";
import { KernelCards } from "@/components/sections/KernelCards";
import { MarketCoverage } from "@/components/sections/MarketCoverage";
import { Principles } from "@/components/sections/Principles";
import { TheLoop } from "@/components/sections/TheLoop";
import { LocaleSwitcher } from "@/components/primitives/LocaleSwitcher";

export default async function HomePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return (
    <main>
      <div className="fixed right-6 top-6 z-50">
        <LocaleSwitcher />
      </div>
      <Hero />
      <TheLoop />
      <KernelCards />
      <Principles />
      <MarketCoverage />
      <EngineeringDiscipline />
      <CTAFooter />
    </main>
  );
}
