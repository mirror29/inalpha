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

/**
 * 06 — Global coverage + current state (transparency).
 * Markets grouped + animated stat row + alpha-quality LiveBadges.
 */
export function GlobalCoverage() {
  const t = useTranslations("coverage");
  const items = t.raw("currentState.items") as string[];

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
                className="bg-bg p-6"
              >
                <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-fg-muted/60">
                  ── {t(`groups.${group}`)} / {tags.length}
                </p>
                <ul className="mt-5 flex flex-wrap gap-2">
                  {tags.map((tag) => (
                    <li
                      key={tag}
                      className="border border-fg/15 px-3 py-1 font-mono text-[11px] uppercase tracking-[0.16em] text-fg-muted transition-colors hover:border-cyan hover:text-cyan"
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
          <Stat target={142} suffix={t("stats.starsSuffix")} accent="text-cyan" />
          <Stat target={23} suffix={t("stats.contributorsSuffix")} />
          <Stat target={487} suffix={t("stats.commitsSuffix")} />
          <Stat target={12} suffix="markets" accent="text-gold" />
          <div className="ml-auto">
            <LiveBadge label={t("stats.qualityLabel")} />
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
    <div className="flex flex-col gap-1.5">
      <StatCounter
        target={target}
        className={`font-mono leading-none tabular-nums text-[clamp(2.25rem,4.4vw,3.75rem)] tracking-tight ${accent}`}
      />
      <span className="font-mono text-[10px] uppercase tracking-[0.26em] text-fg-muted/70">
        ── {suffix}
      </span>
    </div>
  );
}
