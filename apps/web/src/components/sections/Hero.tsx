"use client";

import { ArrowUpRight, BookOpen } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { CopyableCommand } from "@/components/primitives/CopyableCommand";
import { HeroBackdrop } from "@/components/primitives/HeroBackdrop";
import { LocaleSwitcher } from "@/components/primitives/LocaleSwitcher";
import { LINKS } from "@/lib/links";
import { fadeUp } from "@/lib/motion";
import { releaseTag } from "@/lib/release-meta";

/**
 * Page hero — broadsheet aesthetic, refined.
 *
 * Single column. The tagline owns the page. No competing engineering
 * title block on the right (that metadata lives in the ticker strip and
 * footer colophon). Wordmark sits at the very top in mono, small.
 */
export function Hero() {
  const t = useTranslations("hero");
  const tCta = useTranslations("cta");

  return (
    <header className="relative overflow-hidden border-b border-fg/12">
      <HeroBackdrop />

      <div className="absolute right-6 top-6 z-50">
        <LocaleSwitcher />
      </div>

      <div className="relative z-10 mx-auto max-w-6xl px-6 pt-16 pb-24 md:px-12 md:pt-24 md:pb-32">
        <motion.div
          initial="hidden"
          animate="visible"
          variants={{
            hidden: {},
            visible: {
              transition: { staggerChildren: 0.09, delayChildren: 0.08 },
            },
          }}
        >
          <motion.div
            variants={fadeUp}
            className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.32em] text-fg-muted/80"
          >
            <span className="text-fg">Inalpha</span>
            <span className="text-fg-muted/50">/</span>
            <span>open-source quant framework</span>
            <span className="ml-3 hidden h-px w-12 bg-fg/20 sm:inline-block" />
            <span className="hidden text-fg-muted/60 sm:inline">{releaseTag}</span>
          </motion.div>

          <motion.h1
            variants={fadeUp}
            className="display-italic mt-12 text-fg leading-[0.92] md:mt-16"
            style={{ fontSize: "clamp(3rem, 9.5vw, 9rem)", fontWeight: 300 }}
          >
            {t("title")}
            <br />
            <span className="text-cyan">{t("titleAlt")}</span>
          </motion.h1>

          <motion.p
            variants={fadeUp}
            className="mt-10 max-w-[60ch] text-[17px] leading-relaxed text-fg-muted sm:text-[18px]"
          >
            {t("sub")}
          </motion.p>

          <motion.div
            variants={fadeUp}
            className="mt-12 flex flex-wrap items-center gap-x-8 gap-y-4"
          >
            <CopyableCommand
              command={tCta("commands.git")}
              copyLabel={tCta("copy")}
              copiedLabel={tCta("copied")}
              className="min-w-[22rem] max-w-md"
            />
            <Link
              href={LINKS.github}
              target="_blank"
              rel="noreferrer"
              className="group inline-flex items-center gap-2 border-b border-fg/30 pb-1 font-mono text-[12px] uppercase tracking-[0.22em] text-fg-muted transition-colors hover:border-fg hover:text-fg"
            >
              <BookOpen className="size-3.5" />
              {tCta("github")}
              <ArrowUpRight className="size-3.5 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
            </Link>
          </motion.div>
        </motion.div>
      </div>
    </header>
  );
}
