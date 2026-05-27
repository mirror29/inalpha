"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { TerminalBlock } from "@/components/primitives/TerminalBlock";
import { fadeUp, stagger } from "@/lib/motion";

const CHIPS = ["hooks", "permissions", "plan-exec", "subagent", "mcp", "swarm"] as const;

/**
 * 05 — Engineering harness. Permissions config + 6 mechanism chips.
 * Left: real-looking permissions.yaml. Right: harness mechanisms list.
 */
export function EngineeringHarness() {
  const t = useTranslations("harness");

  return (
    <BroadsheetSection
      index="05"
      eyebrow="Engineering harness · claude code, adapted"
      title=""
      titleNode={
        <>
          {t("title")}
          <br />
          <span className="text-cyan/85">{t("titleAlt")}</span>
        </>
      }
      intro={t("sub")}
    >
      <div className="grid gap-8 lg:grid-cols-12">
        <div className="lg:col-span-7">
          <TerminalBlock
            prompt="$"
            caption="permissions.yaml"
            content={t("configSample").split("\n")}
          />
        </div>

        <motion.ul
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-80px" }}
          variants={stagger}
          className="space-y-px bg-fg/10 lg:col-span-5"
        >
          {CHIPS.map((id, idx) => (
            <motion.li
              key={id}
              variants={fadeUp}
              className="group flex items-baseline gap-4 bg-bg p-4 transition-colors hover:bg-bg-deep"
            >
              <span className="font-mono text-[10px] uppercase tracking-[0.22em] text-fg-muted/60">
                {String(idx + 1).padStart(2, "0")}
              </span>
              <div className="min-w-0 flex-1">
                <p className="font-mono text-[13px] uppercase tracking-[0.16em] text-fg">
                  {t(`chips.${id}`)}
                </p>
                <p className="mt-1.5 text-[13.5px] leading-relaxed text-fg-muted">
                  {t(`chipDescs.${id}`)}
                </p>
              </div>
              <span
                aria-hidden
                className="font-mono text-fg-muted/30 transition-colors group-hover:text-cyan"
              >
                ▸
              </span>
            </motion.li>
          ))}
        </motion.ul>
      </div>
    </BroadsheetSection>
  );
}
