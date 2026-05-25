"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { fadeUp, stagger } from "@/lib/motion";

const CHIPS = [
  "hooks",
  "permissions",
  "plan-exec",
  "subagent",
  "mcp",
  "swarm",
] as const;

export function EngineeringDiscipline() {
  const t = useTranslations("discipline");

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
          <motion.p
            variants={fadeUp}
            className="max-w-2xl text-sm text-fg-muted sm:text-base"
          >
            {t("blurb")}
          </motion.p>
        </motion.div>

        <motion.div
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-100px" }}
          variants={stagger}
          className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3"
        >
          {CHIPS.map((id) => (
            <motion.div
              key={id}
              variants={fadeUp}
              className="rounded-lg border border-border-subtle bg-bg-elev/40 p-4 transition-colors hover:border-cyan/40"
            >
              <div className="font-mono text-sm text-cyan">{t(`chips.${id}`)}</div>
              <p className="mt-1.5 text-xs leading-relaxed text-fg-muted">
                {t(`chipDescs.${id}`)}
              </p>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
