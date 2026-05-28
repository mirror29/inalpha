"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { LiveBadge } from "@/components/primitives/LiveBadge";
import { StatCounter } from "@/components/primitives/StatCounter";
import { fadeUp, stagger } from "@/lib/motion";

const TAG_GROUPS = {
  crypto: ["crypto"] as const,
  equities: ["us", "cn", "hk", "jp", "kr", "au", "in", "uk", "de"] as const,
  macro: ["indices", "macro"] as const,
};

const TOTAL_MARKETS = Object.values(TAG_GROUPS).reduce(
  (acc, group) => acc + group.length,
  0,
);

interface GlobalCoverageProps {
  /** Server-side fetched GitHub stats; `null` 时退回静态兜底（仓库当前真实数值）。 */
  stats?: { stars: number; contributors: number; commits: number } | null;
}

/**
 * 06 — Global coverage + current state (transparency).
 * Markets grouped + animated stat row + alpha-quality LiveBadges.
 */
export function GlobalCoverage({ stats }: GlobalCoverageProps = {}) {
  const t = useTranslations("coverage");
  const items = t.raw("currentState.items") as string[];

  // 拿不到 GitHub API（rate-limit / 离线 build）时用一个真实但保守的兜底
  const safeStats = stats ?? { stars: 1, contributors: 1, commits: 170 };

  return (
    <BroadsheetSection
      index="06"
      eyebrow="Coverage · twelve markets, one kernel"
      title={t("title")}
      intro={t("sub")}
    >
      <div className="space-y-12">
        {/* Tag groups */}
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-80px" }}
          variants={stagger}
          className="grid gap-px bg-fg/10 md:grid-cols-3"
        >
          {(Object.entries(TAG_GROUPS) as [keyof typeof TAG_GROUPS, readonly string[]][]).map(
            ([group, tags]) => (
              <motion.div
                key={group}
                variants={fadeUp}
                className="group/coverage relative bg-bg p-6 transition-colors duration-300 hover:bg-bg-deep/60"
              >
                <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-fg-muted/60 transition-colors group-hover/coverage:text-cyan/85">
                  ── {t(`groups.${group}`)} / {tags.length}
                </p>
                <ul className="mt-5 flex flex-wrap gap-2">
                  {tags.map((tag) => (
                    <li
                      key={tag}
                      className="cursor-default border border-fg/15 bg-transparent px-3 py-1 font-mono text-[11px] uppercase tracking-[0.16em] text-fg-muted transition-all duration-200 hover:-translate-y-0.5 hover:scale-[1.04] hover:border-cyan hover:bg-cyan/10 hover:text-cyan hover:shadow-[0_0_14px_-4px_rgba(95,179,255,0.5)]"
                    >
                      {t(`tags.${tag}`)}
                    </li>
                  ))}
                </ul>
              </motion.div>
            ),
          )}
        </motion.div>

        {/* Stat row */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-80px" }}
          transition={{ duration: 0.55 }}
          className="flex flex-wrap items-end gap-x-12 gap-y-6 border-y border-fg/12 py-10"
        >
          <Stat
            target={safeStats.stars}
            suffix={t("stats.starsSuffix")}
            accent="text-cyan"
          />
          <Stat
            target={safeStats.contributors}
            suffix={t("stats.contributorsSuffix")}
          />
          <Stat target={safeStats.commits} suffix={t("stats.commitsSuffix")} />
          <Stat target={TOTAL_MARKETS} suffix="markets" accent="text-gold" />
          <div className="ml-auto">
            <LiveBadge label={t("stats.qualityLabel")} tint="fox" />
          </div>
        </motion.div>

        {/* Current state — transparency callout */}
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-80px" }}
          variants={stagger}
          className="grid grid-cols-12 gap-6 border-l-2 border-gold/60 pl-6"
        >
          <motion.p
            variants={fadeUp}
            className="col-span-12 font-mono text-[11px] uppercase tracking-[0.28em] text-gold md:col-span-3"
          >
            ── {t("currentState.title")}
          </motion.p>
          <ul className="col-span-12 space-y-3 md:col-span-9">
            {items.map((item, idx) => (
              <motion.li
                key={item}
                variants={fadeUp}
                className="flex items-baseline gap-3 text-[14.5px] leading-relaxed text-fg-muted"
              >
                <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-fg-muted/50">
                  {String(idx + 1).padStart(2, "0")}
                </span>
                <span>{item}</span>
              </motion.li>
            ))}
          </ul>
        </motion.div>
      </div>
    </BroadsheetSection>
  );
}

function Stat({
  target,
  suffix,
  accent = "text-fg",
}: {
  target: number;
  suffix: string;
  accent?: string;
}) {
  return (
    <div className="group/stat flex cursor-default flex-col gap-1.5 transition-transform duration-200 hover:-translate-y-0.5">
      <StatCounter
        target={target}
        className={`font-mono leading-none tabular-nums text-[clamp(2.25rem,4.4vw,3.75rem)] tracking-tight transition-[text-shadow,filter] duration-300 group-hover/stat:[text-shadow:0_0_22px_rgba(95,179,255,0.45)] ${accent}`}
      />
      <span className="font-mono text-[10px] uppercase tracking-[0.26em] text-fg-muted/70 transition-colors group-hover/stat:text-fg/90">
        ── {suffix}
      </span>
    </div>
  );
}
