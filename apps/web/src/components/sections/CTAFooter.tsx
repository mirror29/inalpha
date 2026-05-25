"use client";

import { ArrowUpRight } from "lucide-react";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { CopyableCommand } from "@/components/primitives/CopyableCommand";
import { DotGrid } from "@/components/primitives/DotGrid";
import { buttonVariants } from "@/components/ui/button";
import { fadeUp, stagger } from "@/lib/motion";

export function CTAFooter() {
  const t = useTranslations("cta");
  const tf = useTranslations("footer");

  return (
    <section className="relative overflow-hidden py-24 sm:py-32">
      <DotGrid fade="top" />
      <div
        aria-hidden
        className="pointer-events-none absolute -right-32 top-1/3 size-[480px] rounded-full bg-cyan/15 blur-[140px]"
      />

      <div className="relative mx-auto max-w-3xl px-6 text-center">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={stagger}
          className="space-y-5"
        >
          <motion.p
            variants={fadeUp}
            className="font-mono text-xs uppercase tracking-[0.2em] text-cyan/80"
          >
            {t("eyebrow")}
          </motion.p>
          <motion.h2
            variants={fadeUp}
            className="font-mono text-3xl text-fg sm:text-4xl"
          >
            {t("title")}
          </motion.h2>
          <motion.p
            variants={fadeUp}
            className="mx-auto max-w-xl text-sm text-fg-muted sm:text-base"
          >
            {t("blurb")}
          </motion.p>

          <motion.div variants={fadeUp} className="pt-4">
            <CopyableCommand
              command={t("command")}
              copyLabel={t("copy")}
              copiedLabel={t("copied")}
              className="text-left"
            />
          </motion.div>

          <motion.div variants={fadeUp} className="pt-2">
            <a
              href="https://github.com/mirror29/inalpha"
              target="_blank"
              rel="noreferrer"
              className={buttonVariants({ variant: "primary", size: "lg" })}
            >
              {t("github")}
              <ArrowUpRight className="size-4" />
            </a>
          </motion.div>
        </motion.div>
      </div>

      <footer className="relative mt-24 border-t border-border-subtle pt-8">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 pb-10 text-xs text-fg-muted sm:flex-row">
          <p className="font-mono">{tf("rights")}</p>
          <p className="font-mono italic">{tf("tagline")}</p>
          <a
            href="https://github.com/mirror29/inalpha/blob/main/LICENSE"
            target="_blank"
            rel="noreferrer"
            className="font-mono hover:text-cyan"
          >
            {tf("license")}
          </a>
        </div>
      </footer>
    </section>
  );
}
