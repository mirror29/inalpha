"use client";

import Image from "next/image";
import { ArrowUpRight, BookOpen } from "lucide-react";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";

import { DotGrid } from "@/components/primitives/DotGrid";
import { buttonVariants } from "@/components/ui/button";
import { charItem, charStagger, fadeUp, stagger } from "@/lib/motion";

const WORDMARK = "Inalpha";

export function Hero() {
  const t = useTranslations("hero");

  return (
    <section className="relative overflow-hidden">
      <DotGrid fade="radial" />

      <div
        aria-hidden
        className="pointer-events-none absolute -left-32 top-1/3 size-[480px] rounded-full bg-cyan/15 blur-[140px]"
      />

      <div className="relative mx-auto flex min-h-[88vh] max-w-6xl flex-col justify-center px-6 py-24 sm:py-32">
        <motion.div
          initial="hidden"
          animate="visible"
          variants={stagger}
          className="space-y-8"
        >
          <motion.div
            variants={charStagger}
            initial="hidden"
            animate="visible"
            aria-label={WORDMARK}
            className="font-mono text-[clamp(3.5rem,12vw,9rem)] font-medium tracking-tight leading-none"
          >
            {WORDMARK.split("").map((char, i) => (
              <motion.span
                key={i}
                variants={charItem}
                className="inline-block"
              >
                {char}
              </motion.span>
            ))}
          </motion.div>

          <motion.p
            variants={fadeUp}
            className="font-mono text-xl text-cyan sm:text-2xl"
          >
            {t("tagline")}
          </motion.p>

          <motion.p
            variants={fadeUp}
            className="max-w-2xl text-base text-fg-muted sm:text-lg"
          >
            {t("subtitle")}
          </motion.p>

          <motion.p
            variants={fadeUp}
            className="max-w-2xl text-sm leading-relaxed text-fg-muted/80 sm:text-base"
          >
            {t("blurb")}
          </motion.p>

          <motion.div variants={fadeUp} className="flex flex-wrap gap-3 pt-4">
            <a
              href="https://github.com/mirror29/inalpha"
              target="_blank"
              rel="noreferrer"
              className={buttonVariants({ variant: "primary", size: "lg" })}
            >
              {t("cta.github")}
              <ArrowUpRight className="size-4" />
            </a>
            <a
              href="https://github.com/mirror29/inalpha#readme"
              target="_blank"
              rel="noreferrer"
              className={buttonVariants({ variant: "ghost", size: "lg" })}
            >
              <BookOpen className="size-4" />
              {t("cta.docs")}
            </a>
          </motion.div>
        </motion.div>
      </div>

      <Image
        src="/mascot.png"
        alt=""
        aria-hidden
        width={120}
        height={120}
        priority
        className="pointer-events-none absolute bottom-8 right-6 size-24 opacity-60 mix-blend-screen sm:size-32 lg:right-12"
      />
    </section>
  );
}
