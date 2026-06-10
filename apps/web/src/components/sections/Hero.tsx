"use client";

import { ArrowUpRight, BookOpen } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { CopyableCommand } from "@/components/primitives/CopyableCommand";
import { HeroAtmosphere } from "@/components/primitives/HeroAtmosphere";
import { HeroScene } from "@/components/primitives/HeroScene";
import { LocaleSwitcher } from "@/components/primitives/LocaleSwitcher";
import { ThemeToggle } from "@/components/primitives/ThemeToggle";
import { LINKS } from "@/lib/links";
import { fadeUp } from "@/lib/motion";

const EASE = [0.22, 0.7, 0.22, 1] as const;

/** 标题遮罩上升 —— 父级 overflow-hidden，标题从下方升入。 */
const riseMask = {
  hidden: { y: "115%" },
  visible: { y: "0%", transition: { duration: 0.8, ease: EASE } },
};

/** 朱红线条横向展开（origin-left 由 className 给）。 */
const drawRule = {
  hidden: { scaleX: 0, opacity: 0 },
  visible: { scaleX: 1, opacity: 1, transition: { duration: 0.6, ease: EASE } },
};

/**
 * Page hero — 「神社官報 / Shrine Gazette」。
 *
 * 背景 = 呼吸雾光（HeroAtmosphere）+ canvas 主场景「関所の帳簿」（HeroScene）：
 * 行情线从左流入 → 穿过朱红鸟居（审批关门）→ 落朱印出回执 → 过门转 bull 绿。
 * 左侧雾气留白上叠诗性 tagline 领衔，工程主张作支撑句。氛围层做美，
 * 数据区（下方各 section）仍临床（DESIGN.md §3.4）。
 */
export function Hero() {
  const t = useTranslations("hero");
  const tCta = useTranslations("cta");

  return (
    <header className="relative isolate overflow-hidden border-b border-fg/12">
      {/* 纯氛围底 —— 呼吸雾光 + 偶尔光线掠过，叠在 dot-grid/grain 纹理上 */}
      <div className="absolute inset-0 -z-10 overflow-hidden">
        <HeroAtmosphere className="absolute inset-0" />
        {/* 极淡点阵作底纹，呼应 broadsheet 图纸感 */}
        <div className="dot-grid absolute inset-0 opacity-[0.3]" aria-hidden />
        {/* 主场景：行情线穿鸟居落印（canvas） */}
        <HeroScene className="absolute inset-0" />
        {/* 底部羽化融入页面 */}
        <div
          className="absolute inset-x-0 bottom-0 h-24"
          style={{
            background: "linear-gradient(to bottom, transparent, var(--surface))",
          }}
        />
      </div>

      <div className="absolute right-6 top-6 z-50 flex items-center gap-2">
        <ThemeToggle />
        <LocaleSwitcher />
      </div>

      <div className="relative z-10 mx-auto flex min-h-160 max-w-[88rem] flex-col justify-center px-4 py-24 md:min-h-[86vh] md:px-14">
        <motion.div
          className="w-full max-w-132 md:max-w-180"
          initial="hidden"
          animate="visible"
          variants={{
            hidden: {},
            visible: { transition: { staggerChildren: 0.11, delayChildren: 0.08 } },
          }}
        >
          {/* 名字故事 eyebrow —— 暖调，不用工程 mono 大写 */}
          <motion.div
            variants={fadeUp}
            className="flex items-center gap-3 text-[13px] text-fg-muted"
          >
            <span className="font-mono uppercase tracking-[0.28em] text-fg">
              Inalpha
            </span>
            <motion.span
              variants={drawRule}
              className="h-px w-8 origin-left bg-seal/50"
            />
            <span className="italic">Inari × alpha</span>
          </motion.div>

          {/* 诗性 tagline 领衔（匾額一道朱红线条展开 + 标题遮罩上升） */}
          <div className="mt-7 md:mt-9">
            <motion.span
              variants={drawRule}
              className="block h-px w-14 origin-left bg-seal/60"
            />
            <div className="mt-5 overflow-hidden pb-[0.12em]">
              <motion.h1
                variants={riseMask}
                className="display text-fg"
                style={{
                  fontSize: "clamp(2.5rem, 6.4vw, 5rem)",
                  lineHeight: 1.04,
                  fontWeight: 360,
                }}
              >
                {t("tagline")}
              </motion.h1>
            </div>
          </div>

          {/* 工程主张作支撑句 */}
          <motion.p
            variants={fadeUp}
            className="mt-6 font-mono text-[13px] uppercase tracking-[0.16em] text-seal/90"
          >
            {t("title")} {t("titleAlt")}
          </motion.p>

          <motion.p
            variants={fadeUp}
            className="mt-6 max-w-[52ch] text-[16.5px] leading-relaxed text-fg-muted sm:text-[17px]"
          >
            {t("sub")}
          </motion.p>

          <motion.div
            variants={fadeUp}
            className="mt-10 flex flex-wrap items-center gap-x-8 gap-y-4"
          >
            <CopyableCommand
              command={tCta("commands.git")}
              copyLabel={tCta("copy")}
              copiedLabel={tCta("copied")}
              className="min-w-[19rem] max-w-md"
            />
            <Link
              href={LINKS.github}
              target="_blank"
              rel="noreferrer"
              className="group inline-flex items-center gap-2 border-b border-fg/30 pb-1 font-mono text-[12px] uppercase tracking-[0.22em] text-fg-muted transition-colors hover:border-fg hover:text-fg"
            >
              <BookOpen className="size-3.5" />
              {tCta("github")}
              <ArrowUpRight className="size-3.5 transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
            </Link>
          </motion.div>
        </motion.div>
      </div>
    </header>
  );
}
