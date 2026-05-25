"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { fadeUp, stagger } from "@/lib/motion";

const TAGS = [
  "crypto",
  "us",
  "cn",
  "hk",
  "jp",
  "kr",
  "au",
  "in",
  "uk",
  "de",
  "indices",
  "macro",
] as const;

export function MarketCoverage() {
  const t = useTranslations("markets");

  return (
    <section className="relative py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-6">
        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={stagger}
          className="space-y-4"
        >
          <motion.p
            variants={fadeUp}
            className="font-mono text-xs uppercase tracking-[0.2em] text-cyan/80"
          >
            {t("eyebrow")}
          </motion.p>
          <motion.h2
            variants={fadeUp}
            className="font-mono text-2xl text-fg sm:text-3xl"
          >
            {t("title")}
          </motion.h2>
          <motion.p
            variants={fadeUp}
            className="max-w-2xl text-sm text-fg-muted sm:text-base"
          >
            {t("blurb")}
          </motion.p>
        </motion.div>

        <motion.ul
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={stagger}
          className="mt-10 flex flex-wrap gap-2"
        >
          {TAGS.map((tag) => (
            <motion.li
              key={tag}
              variants={fadeUp}
              className="rounded-full border border-border-subtle bg-bg-elev/40 px-3.5 py-1.5 font-mono text-xs text-fg-muted transition-colors hover:border-cyan/60 hover:text-cyan"
            >
              {t(`tags.${tag}`)}
            </motion.li>
          ))}
        </motion.ul>
      </div>
    </section>
  );
}
