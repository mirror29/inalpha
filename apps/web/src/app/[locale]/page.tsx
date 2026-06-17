import { setRequestLocale } from "next-intl/server";

import { AgentIntelligence } from "@/components/sections/AgentIntelligence";
import { CoreWedge } from "@/components/sections/CoreWedge";
import { CTAFooter } from "@/components/sections/CTAFooter";
import { FAQ } from "@/components/sections/FAQ";
import { GlobalCoverage } from "@/components/sections/GlobalCoverage";
import { Hero } from "@/components/sections/Hero";
import { ResearchFloor } from "@/components/sections/ResearchFloor";
import { StrategyEvolution } from "@/components/sections/StrategyEvolution";
import { TrustBoundary } from "@/components/sections/TrustBoundary";
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

  // build 时抓真实数字随 HTML 出厂（静态导出：更新频率 = 部署频率）；
  // 拉取失败返 null，CTAFooter 整组隐藏，不放假数字
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
    "AN ORACLE THAT KEEPS A LEDGER",
    "FACTOR TIMING · RANK IC",
    "INVESTING LEGENDS PANEL",
    "STRATEGY EVOLUTION",
    "70-FACTOR LIBRARY · DECAY WATCH",
    "PLAN · APPROVE · EXECUTE",
    "POSITION GUARD · −20% HARD STOP",
    "AUTONOMOUS PAPER RUNNER",
    "AGENTS · FIRST-CLASS",
    "AGPL-3.0",
    "12 MARKETS",
    "INARI OMIKUJI",
  ];

  return (
    <div className="relative min-h-screen grain bg-bg text-fg">
      <TickerStrip items={tickerItems} />

      <Hero />

      <main className="mx-auto max-w-[96rem] space-y-28 px-6 py-24 md:space-y-40 md:px-14 md:py-28">
        <CoreWedge />
        <AgentIntelligence />
        <ResearchFloor />
        <StrategyEvolution />
        <UnifiedKernel />
        <TrustBoundary />
        <GlobalCoverage />
        <FAQ />
      </main>

      <CTAFooter stats={githubStats} />
    </div>
  );
}
