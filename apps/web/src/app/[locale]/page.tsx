import { setRequestLocale } from "next-intl/server";

import { BlackBoxProblem } from "@/components/sections/BlackBoxProblem";
import { CTAFooter } from "@/components/sections/CTAFooter";
import { DualThesis } from "@/components/sections/DualThesis";
import { EngineeringHarness } from "@/components/sections/EngineeringHarness";
import { GlobalCoverage } from "@/components/sections/GlobalCoverage";
import { Hero } from "@/components/sections/Hero";
import { SystemSchematic } from "@/components/sections/SystemSchematic";
import { UnifiedKernel } from "@/components/sections/UnifiedKernel";
import { TickerStrip } from "@/components/primitives/TickerStrip";
import { getGithubStats } from "@/lib/github";
import { REPO_COORDS } from "@/lib/links";
import { RELEASE } from "@/lib/release-meta";

export default async function HomePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  // server-side fetch；1h ISR；失败时 GlobalCoverage 自己走兜底
  const githubStats = await getGithubStats({
    owner: REPO_COORDS.owner,
    repo: REPO_COORDS.name,
  });

  const tickerItems = [
    "INALPHA",
    "OPEN-SOURCE QUANT AGENT FRAMEWORK",
    RELEASE.phase,
    `REV ${RELEASE.rev}`,
    RELEASE.dateDot,
    "AUDIT-GRADE EVOLUTION",
    "FACTOR LAB · RISK ENGINE",
    "PLAN · APPROVE · EXECUTE",
    "AGENTS · FIRST-CLASS",
    "AGPL-3.0",
    "ALPHA QUALITY",
    "12 MARKETS",
  ];

  return (
    <div className="relative min-h-screen grain bg-bg text-fg">
      <TickerStrip items={tickerItems} />

      <Hero />

      <main className="mx-auto max-w-6xl space-y-28 px-6 py-24 md:space-y-36 md:px-12 md:py-28">
        <BlackBoxProblem />
        <DualThesis />
        <SystemSchematic />
        <UnifiedKernel />
        <EngineeringHarness />
        <GlobalCoverage stats={githubStats} />
      </main>

      <CTAFooter />
    </div>
  );
}
