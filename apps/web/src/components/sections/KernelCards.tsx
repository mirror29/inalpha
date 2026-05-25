"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { fadeUp, stagger } from "@/lib/motion";

const KERNELS = ["data", "paper", "research"] as const;

export function KernelCards() {
  const t = useTranslations("kernels");

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
        </motion.div>

        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={stagger}
          className="mt-12 grid gap-5 md:grid-cols-3"
        >
          {KERNELS.map((id) => (
            <motion.article
              key={id}
              variants={fadeUp}
              className="group relative overflow-hidden rounded-xl border border-border-subtle bg-bg-elev/40 p-6 transition-colors hover:border-cyan/40"
            >
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-2xl text-cyan">
                  {t(`${id}.title`)}
                </span>
                <span className="font-mono text-xs text-fg-muted">/ python</span>
              </div>
              <p className="mt-4 text-sm leading-relaxed text-fg-muted">
                {t(`${id}.desc`)}
              </p>
              <pre className="mt-6 overflow-x-auto rounded-md border border-border-subtle bg-bg/60 px-3 py-2.5 font-mono text-[12px] text-fg-muted">
                <code>{t(`${id}.code`)}</code>
              </pre>
            </motion.article>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
