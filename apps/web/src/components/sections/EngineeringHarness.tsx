"use client";

import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { TerminalBlock } from "@/components/primitives/TerminalBlock";
import { cn } from "@/lib/cn";
import { fadeUp, stagger } from "@/lib/motion";

const CHIPS = ["permissions", "hooks", "plan-exec", "subagent", "mcp", "swarm"] as const;
type Chip = (typeof CHIPS)[number];

/**
 * 05 — Engineering harness. Interactive: 右侧 6 个机制 chip 点击后联动左侧终端，
 * 展示对应真实片段（permissions yaml / hook handler / plan-exec trace 等）。
 */
export function EngineeringHarness() {
  const t = useTranslations("harness");
  const [active, setActive] = useState<Chip>("permissions");
  // 用 t.raw 拿原始字符串，避免 ICU 把样本里 `{ planId, ... }` 当作占位符解析
  const sampleLines = (t.raw(`samples.${active}`) as string).split("\n");
  // 全部 chip 共享最大行高，切 chip 不会跳动
  const maxLines = CHIPS.reduce(
    (m, c) => Math.max(m, (t.raw(`samples.${c}`) as string).split("\n").length),
    0,
  );

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
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={active}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.2, ease: "easeOut" }}
            >
              <TerminalBlock
                caption={t(`captions.${active}`)}
                content={sampleLines}
                typewriter
                minLines={maxLines}
              />
            </motion.div>
          </AnimatePresence>
          <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.22em] text-fg-muted/55">
            {t("hint")}
          </p>
        </div>

        <motion.ul
          initial="hidden"
          whileInView="visible"
          viewport={{ once: true, margin: "-80px" }}
          variants={stagger}
          className="space-y-px bg-fg/10 lg:col-span-5"
          role="tablist"
          aria-label="harness mechanisms"
        >
          {CHIPS.map((id, idx) => {
            const isActive = active === id;
            return (
              <motion.li key={id} variants={fadeUp}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  aria-controls="harness-terminal"
                  onClick={() => setActive(id)}
                  className={cn(
                    "group relative flex w-full items-baseline gap-4 p-4 text-left transition-colors",
                    isActive
                      ? "bg-bg-deep"
                      : "bg-bg hover:bg-bg-deep",
                  )}
                >
                  {isActive ? (
                    <span
                      aria-hidden
                      className="absolute left-0 top-0 h-full w-px bg-cyan"
                    />
                  ) : null}
                  <span
                    className={cn(
                      "font-mono text-[10px] uppercase tracking-[0.22em]",
                      isActive ? "text-cyan" : "text-fg-muted/60",
                    )}
                  >
                    {String(idx + 1).padStart(2, "0")}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p
                      className={cn(
                        "font-mono text-[13px] uppercase tracking-[0.16em]",
                        isActive ? "text-cyan" : "text-fg",
                      )}
                    >
                      {t(`chips.${id}`)}
                    </p>
                    <p className="mt-1.5 text-[13.5px] leading-relaxed text-fg-muted">
                      {t(`chipDescs.${id}`)}
                    </p>
                  </div>
                  <span
                    aria-hidden
                    className={cn(
                      "font-mono transition-colors",
                      isActive
                        ? "text-cyan"
                        : "text-fg-muted/30 group-hover:text-cyan",
                    )}
                  >
                    {isActive ? "▾" : "▸"}
                  </span>
                </button>
              </motion.li>
            );
          })}
        </motion.ul>
      </div>
    </BroadsheetSection>
  );
}
