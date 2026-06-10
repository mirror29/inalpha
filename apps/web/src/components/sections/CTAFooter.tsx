"use client";

import { ArrowUpRight } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { CopyableCommand } from "@/components/primitives/CopyableCommand";
import { LiveBadge } from "@/components/primitives/LiveBadge";
import { StatCounter } from "@/components/primitives/StatCounter";
import { LINKS } from "@/lib/links";
import { fadeUp } from "@/lib/motion";
import { releaseFootline } from "@/lib/release-meta";

const TOTAL_MARKETS = 12;

interface CTAFooterProps {
  /** Server-side fetched GitHub stats; null 时退回保守兜底。 */
  stats?: { stars: number; contributors: number; commits: number } | null;
}

/**
 * 09 — Closing CTA + GitHub 数字 + footer。
 * Get started 下展示仓库的 star / contributor / commit / markets + alpha 标，
 * 与「star it · read it」的号召自然成对。
 */
export function CTAFooter({ stats }: CTAFooterProps = {}) {
  const t = useTranslations("cta");
  const tf = useTranslations("footer");
  const tc = useTranslations("coverage");
  const safeStats = stats ?? { stars: 1, contributors: 1, commits: 287 };

  return (
    <section className="group relative isolate overflow-hidden border-t border-fg/12">
      <span
        aria-hidden
        className="pointer-events-none absolute right-2 top-2 -z-10 select-none font-display italic leading-none text-fg/[0.04] transition-colors duration-500 group-hover:text-gold/25"
        style={{ fontSize: "clamp(7rem, 20vw, 18rem)" }}
      >
        09
      </span>
      <div className="mx-auto max-w-[88rem] px-6 pt-16 pb-20 md:px-12 md:pt-20 md:pb-24">
        {/* Bracketed header */}
        <div className="border-y border-fg/15">
          <div className="flex items-center justify-between gap-6 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
            <span className="flex items-center gap-2.5">
              <span
                className="inline-block h-3 w-[2px] bg-seal/70"
                aria-hidden="true"
              />
              <span>{t("eyebrow")}</span>
            </span>
            <span className="text-fg-muted/50">agpl-3.0 · audited · open</span>
          </div>
        </div>

        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={{ hidden: {}, visible: { transition: { staggerChildren: 0.08 } } }}
          className="pt-12"
        >
          <motion.h2
            variants={fadeUp}
            className="display-italic max-w-[20ch] text-fg leading-[1.02]"
            style={{ fontSize: "clamp(2.25rem, 4.8vw, 3.75rem)", fontWeight: 400 }}
          >
            {t("title")}
          </motion.h2>
          <motion.p
            variants={fadeUp}
            className="mt-6 max-w-[62ch] text-[15px] leading-relaxed text-fg-muted"
          >
            {t("sub")}
          </motion.p>

          <motion.div variants={fadeUp} className="mt-12 max-w-xl">
            <CopyableCommand
              command={t("commands.git")}
              copyLabel={t("copy")}
              copiedLabel={t("copied")}
            />
          </motion.div>

          <motion.div
            variants={fadeUp}
            className="mt-8 flex flex-wrap items-center gap-x-8 gap-y-3"
          >
            <Link
              href={LINKS.github}
              target="_blank"
              rel="noreferrer"
              className="group inline-flex items-center gap-2 border border-fg/20 px-5 py-2.5 font-mono text-[12px] uppercase tracking-[0.22em] text-fg transition-colors hover:border-cyan hover:text-cyan"
            >
              {t("github")}
              <ArrowUpRight className="size-3.5 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
            </Link>
            <Link
              href={LINKS.license}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted underline-offset-4 hover:text-fg hover:underline"
            >
              {tf("license")}
            </Link>
          </motion.div>

          {/* GitHub 数字 */}
          <motion.div
            variants={fadeUp}
            className="mt-12 flex flex-wrap items-end gap-x-12 gap-y-6 border-t border-fg/12 pt-10"
          >
            <Stat target={safeStats.stars} suffix={tc("stats.starsSuffix")} accent="text-cyan" />
            <Stat target={safeStats.contributors} suffix={tc("stats.contributorsSuffix")} />
            <Stat target={safeStats.commits} suffix={tc("stats.commitsSuffix")} />
            <Stat target={TOTAL_MARKETS} suffix="markets" accent="text-gold" />
            <div className="ml-auto">
              <LiveBadge label={tc("stats.qualityLabel")} tint="fox" />
            </div>
          </motion.div>
        </motion.div>
      </div>

      {/* Footer colophon */}
      <footer className="border-t border-fg/12">
        <div className="mx-auto grid max-w-[88rem] grid-cols-12 gap-x-6 gap-y-4 px-6 py-10 font-mono text-[10px] uppercase tracking-[0.26em] text-fg-muted/70 md:px-12">
          <div className="col-span-6 md:col-span-3">
            <p className="text-fg/40">file</p>
            <p className="mt-1.5 text-fg-muted">inalpha.dev</p>
          </div>
          <div className="col-span-6 md:col-span-3">
            <p className="text-fg/40">spec</p>
            <p className="mt-1.5 text-fg-muted">DESIGN.md §10</p>
          </div>
          <div className="col-span-6 md:col-span-3">
            <p className="text-fg/40">rev</p>
            <p className="mt-1.5 text-fg-muted">{releaseFootline}</p>
          </div>
          <div className="col-span-6 md:col-span-3">
            <p className="text-fg/40">© rights</p>
            <p className="mt-1.5 text-fg-muted">{tf("rights")}</p>
          </div>

          {/* 神社收尾：名字故事（暖 sans）+ oracle 匾額（朱红） */}
          <div className="col-span-12 mt-4 border-t border-fg/10 pt-5 text-right">
            <p className="ml-auto max-w-[68ch] font-sans text-[13.5px] normal-case leading-relaxed tracking-normal text-fg-muted/80">
              {tf("nameStory")}
            </p>
            <p className="mt-3 normal-case tracking-[0.16em] text-seal/80">
              ── {tf("oracle")}
            </p>
          </div>
        </div>
      </footer>
    </section>
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
