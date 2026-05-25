"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { fadeUp, stagger } from "@/lib/motion";

const ITEMS = [
  "unified",
  "agents",
  "transparency",
  "discipline",
  "compounding",
] as const;

export function Principles() {
  const t = useTranslations("principles");

  return (
    <section className="relative border-y border-border-subtle bg-bg-elev/20 py-24 sm:py-32">
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

        <motion.ul
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={stagger}
          className="mt-12 grid gap-px overflow-hidden rounded-xl border border-border-subtle bg-border-subtle md:grid-cols-2"
        >
          {ITEMS.map((id, idx) => (
            <motion.li
              key={id}
              variants={fadeUp}
              className={`bg-bg p-6 sm:p-8 ${idx === ITEMS.length - 1 ? "md:col-span-2" : ""}`}
            >
              <div className="flex items-start gap-4">
                <span className="mt-1.5 font-mono text-[11px] text-cyan/70">
                  {String(idx + 1).padStart(2, "0")}
                </span>
                <div className="space-y-2">
                  <h3 className="font-mono text-lg text-fg">
                    {t(`items.${id}.title`)}
                  </h3>
                  <p className="text-sm leading-relaxed text-fg-muted">
                    {t(`items.${id}.desc`)}
                  </p>
                </div>
              </div>
            </motion.li>
          ))}
        </motion.ul>
      </div>
    </section>
  );
}
