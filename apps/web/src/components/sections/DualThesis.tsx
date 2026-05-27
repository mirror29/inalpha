"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { BroadsheetSection } from "@/components/primitives/BroadsheetSection";
import { fadeUp, slideInTilt } from "@/lib/motion";

/**
 * 02 — Two non-negotiables. A (agents first-class) + B (engineering discipline).
 * Both must hold. Hairline-edged columns, accent tick on the side.
 */
export function DualThesis() {
  const t = useTranslations("thesis");
  const itemsA = t.raw("columnA.items") as string[];
  const itemsB = t.raw("columnB.items") as string[];

  return (
    <BroadsheetSection
      index="02"
      eyebrow={t("eyebrow")}
      title=""
      titleNode={
        <>
          {t("title")}
          <br />
          <span className="text-cyan">{t("titleAlt")}</span>
        </>
      }
    >
      <motion.div
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, margin: "-100px" }}
        variants={{
          hidden: {},
          visible: { transition: { staggerChildren: 0.15 } },
        }}
        className="grid gap-px bg-fg/10 md:grid-cols-2"
      >
        <ThesisColumn
          letter="A"
          header={t("columnA.header")}
          items={itemsA}
          footer={t("columnA.footer")}
          accent="cyan"
          direction={-1}
        />
        <ThesisColumn
          letter="B"
          header={t("columnB.header")}
          items={itemsB}
          footer={t("columnB.footer")}
          accent="gold"
          direction={1}
        />
      </motion.div>
      <motion.p
        initial={{ opacity: 0, y: 12 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-100px" }}
        transition={{ duration: 0.55, delay: 0.45 }}
        className="mt-10 max-w-[64ch] font-mono text-[12px] uppercase leading-relaxed tracking-[0.18em] text-fg-muted"
      >
        ─── {t("conclusion")}
      </motion.p>
    </BroadsheetSection>
  );
}

function ThesisColumn({
  letter,
  header,
  items,
  footer,
  accent,
  direction,
}: {
  letter: "A" | "B";
  header: string;
  items: string[];
  footer: string;
  accent: "cyan" | "gold";
  direction: -1 | 1;
}) {
  const accentColor = accent === "cyan" ? "text-cyan" : "text-gold";
  const accentBar = accent === "cyan" ? "bg-cyan" : "bg-gold";
  const dotColor = accent === "cyan" ? "bg-cyan/70" : "bg-gold/70";
  return (
    <motion.article
      variants={slideInTilt}
      custom={direction}
      className="group relative flex flex-col gap-8 bg-bg p-8 md:p-10"
    >
      <span
        aria-hidden
        className={`absolute left-0 top-0 h-full w-px ${accentBar} opacity-70`}
      />
      <header className="space-y-3">
        <p
          className={`font-mono text-[11px] uppercase tracking-[0.32em] ${accentColor}`}
        >
          {letter}.
        </p>
        <h3
          className="display-italic leading-[0.95] text-fg"
          style={{ fontSize: "clamp(1.5rem, 2.6vw, 2.25rem)", fontWeight: 400 }}
        >
          {header}
        </h3>
      </header>
      <ul className="space-y-3.5">
        {items.map((item) => (
          <li
            key={item}
            className="group/item flex items-start gap-3 text-[14.5px] leading-relaxed text-fg-muted transition-colors hover:text-fg"
          >
            <span
              aria-hidden
              className={`mt-2 inline-block size-1.5 shrink-0 rounded-full ${dotColor} transition-transform group-hover/item:scale-125`}
            />
            <span>{item}</span>
          </li>
        ))}
      </ul>
      <footer className="mt-auto border-t border-fg/10 pt-5 font-mono text-[11px] uppercase tracking-[0.18em] text-fg-muted">
        ── {footer}
      </footer>
    </motion.article>
  );
}
