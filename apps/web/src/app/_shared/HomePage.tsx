
import { AgentIntelligence } from "@/components/sections/AgentIntelligence";
import { CoreWedge } from "@/components/sections/CoreWedge";
import { CTAFooter } from "@/components/sections/CTAFooter";
import { FAQ } from "@/components/sections/FAQ";
import { GlobalCoverage } from "@/components/sections/GlobalCoverage";
import { Hero } from "@/components/sections/Hero";
import { OverfittingGuard } from "@/components/sections/OverfittingGuard";
import { ResearchFloor } from "@/components/sections/ResearchFloor";
import { StrategyEvolution } from "@/components/sections/StrategyEvolution";
import { TrustBoundary } from "@/components/sections/TrustBoundary";
import { UnifiedKernel } from "@/components/sections/UnifiedKernel";
import { TickerStrip } from "@/components/primitives/TickerStrip";
import { getGithubStats } from "@/lib/github";
import { REPO_COORDS } from "@/lib/links";
import { getUpdateTag } from "@/lib/release-meta";
import {
  buildHomeStructuredData,
  type SupportedLocale,
} from "@/lib/seo";

export default async function HomePage({
  locale,
}: {
  locale: SupportedLocale;
}) {
  const githubStats = await getGithubStats({
    owner: REPO_COORDS.owner,
    repo: REPO_COORDS.name,
  });
  const updateTag = await getUpdateTag();
  const tickerItems = [
    "INALPHA",
    "OPEN-SOURCE QUANT AGENT FRAMEWORK",
    updateTag,
    "AN ORACLE THAT KEEPS A LEDGER",
    "FACTOR TIMING · RANK IC",
    "INVESTING LEGENDS PANEL",
    "STRATEGY EVOLUTION",
    "ANTI-OVERFITTING · CPCV · DEFLATED SHARPE",
    "79-FACTOR LIBRARY · DECAY WATCH",
    "PLAN · APPROVE · EXECUTE",
    "POSITION GUARD · −20% HARD STOP",
    "AUTONOMOUS PAPER RUNNER",
    "AGENTS · FIRST-CLASS",
    "AGPL-3.0",
    "12 MARKETS",
    "INARI OMIKUJI",
  ];
  const structuredData = buildHomeStructuredData(locale);

  return (
    <>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structuredData) }}
      />
      <div className="relative min-h-screen grain bg-bg text-fg">
        <TickerStrip items={tickerItems} />
        <Hero />
        <main className="mx-auto max-w-[96rem] space-y-28 px-6 py-24 md:space-y-40 md:px-14 md:py-28">
          <CoreWedge />
          <AgentIntelligence />
          <ResearchFloor />
          <StrategyEvolution />
          <OverfittingGuard />
          <UnifiedKernel />
          <TrustBoundary />
          <GlobalCoverage />
          <FAQ />
        </main>
        <CTAFooter stats={githubStats} updateTag={updateTag} />
      </div>
    </>
  );
}
