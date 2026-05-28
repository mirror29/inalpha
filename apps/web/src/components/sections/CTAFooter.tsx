"use client";

import { ArrowUpRight } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { CopyableCommand } from "@/components/primitives/CopyableCommand";
import { LINKS } from "@/lib/links";
import { fadeUp } from "@/lib/motion";
import { releaseFootline } from "@/lib/release-meta";

/**
 * 07 — Closing CTA + footer.
 * Same bracketed-header rhythm as the other broadsheet sections so the
 * page closes the way it opens. Single `git clone` command — no pip yet
 * (package not published to PyPI, kept off the page to avoid copy-paste 404s).
 */
export function CTAFooter() {
  const t = useTranslations("cta");
  const tf = useTranslations("footer");

  return (
    <section className="relative border-t border-fg/12">
      <div className="mx-auto max-w-6xl px-6 pt-16 pb-20 md:px-12 md:pt-20 md:pb-24">
        {/* Bracketed header */}
        <div className="border-y border-fg/15">
          <div className="flex items-center justify-between gap-6 py-3 font-mono text-[11px] uppercase tracking-[0.22em] text-fg-muted">
            <span>
              <span className="text-fg/90">07</span>
              <span className="text-fg-muted/50"> / </span>
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
        </motion.div>
      </div>

      {/* Footer colophon */}
      <footer className="border-t border-fg/12">
        <div className="mx-auto grid max-w-6xl grid-cols-12 gap-x-6 gap-y-4 px-6 py-10 font-mono text-[10px] uppercase tracking-[0.26em] text-fg-muted/70 md:px-12">
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

          <p className="col-span-12 mt-4 border-t border-fg/10 pt-4 text-fg/30">
            ── {tf("tagline")}
          </p>
        </div>
      </footer>
    </section>
  );
}
