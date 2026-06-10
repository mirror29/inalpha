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
import { cn } from "@/lib/cn";
import { LINKS } from "@/lib/links";
import { fadeUp } from "@/lib/motion";

const EASE = [0.22, 0.7, 0.22, 1] as const;

/** 标题逐字 / 逐词级联上升 —— 父级 overflow-hidden 作遮罩。 */
const riseStagger = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.05, delayChildren: 0.1 } },
};

/** 单个字 / 词：从遮罩下方带微倾上升落定（落子感）。 */
const riseChar = {
  hidden: { y: "118%", opacity: 0, rotate: 4 },
  visible: {
    y: "0%",
    opacity: 1,
    rotate: 0,
    transition: { duration: 0.7, ease: EASE },
  },
};

/** 朱红线条横向展开（origin-left 由 className 给）。 */
const drawRule = {
  hidden: { scaleX: 0, opacity: 0 },
  visible: { scaleX: 1, opacity: 1, transition: { duration: 0.6, ease: EASE } },
};

/**
 * Page hero — 「神社官報 / Shrine Gazette」。
 *
 * 背景 = 呼吸雾光（HeroAtmosphere）+ canvas 主场景「关卡账簿」（HeroScene）：
 * 行情线从左流入 → 穿过朱红审批阈线（机器审批关卡）→ 落朱印出回执入账簿 →
 * 过线后按 verdict 转 bull 绿 / bear 红。
 * 左侧雾气留白上叠诗性 tagline 领衔，工程主张作支撑句。氛围层做美，
 * 数据区（下方各 section）仍临床（DESIGN.md §3.4）。
 */
export function Hero() {
  const t = useTranslations("hero");
  const tCta = useTranslations("cta");

  const tagline = t("tagline");
  /* 末尾标点拆出来作朱红「落款」；slogan 永远单行不换行，字号按视觉宽度
     估算（CJK ≈ 1em / 拉丁 ≈ 0.52em / 空格 ≈ 0.3em）自动缩放到放得下。
     拆分粒度：短句（CJK 一类）逐字入场，带空格的长句逐词。不按语言硬编码。 */
  const m = tagline.match(/^(.*?)([。．.!！?？])\s*$/);
  const body = m ? m[1] : tagline;
  const tail = m ? m[2] : "";
  const compact = Array.from(tagline).length <= 14;
  const units = compact ? Array.from(body) : body.split(/\s+/).filter(Boolean);
  const emLen = Array.from(tagline).reduce(
    (s, ch) =>
      s + (/[⺀-鿿豈-﫿＀-￯]/.test(ch) ? 1 : /\s/.test(ch) ? 0.3 : 0.52),
    0
  );

  return (
    <header className="relative isolate overflow-hidden border-b border-fg/12">
      {/* 纯氛围底 —— 呼吸雾光 + 偶尔光线掠过，叠在 dot-grid/grain 纹理上 */}
      <div className="absolute inset-0 -z-10 overflow-hidden">
        <HeroAtmosphere className="absolute inset-0" />
        {/* 极淡点阵作底纹，呼应 broadsheet 图纸感 */}
        <div className="dot-grid absolute inset-0 opacity-[0.3]" aria-hidden />
        {/* 主场景：行情线穿审批阈线落印（canvas） */}
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
              className="rule-breathe h-px w-8 origin-left bg-seal/50"
            />
            <span className="italic">Inari × alpha</span>
          </motion.div>

          {/* 诗性 tagline 领衔（匾額一道朱红线条展开 + 标题遮罩上升） */}
          <div className="mt-7 md:mt-9">
            <motion.span
              variants={drawRule}
              className="rule-breathe block h-px w-14 origin-left bg-seal/60"
            />
            <div className="mt-5 w-max max-w-none overflow-hidden pb-[0.12em]">
              <motion.h1
                variants={riseStagger}
                className="display text-sheen whitespace-nowrap text-fg"
                style={{
                  /* 62vw ≈ 左起到审批阈线前的可用宽度，按文案 em 宽反推字号 */
                  fontSize: `clamp(1.4rem, ${(62 / emLen).toFixed(2)}vw, 4.6rem)`,
                  lineHeight: 1.04,
                  fontWeight: 500,
                }}
              >
                {units.map((u, i) => (
                  <motion.span key={i} variants={riseChar} className="inline-block">
                    {u}
                    {/* 词间距用 nbsp —— inline-block 末尾普通空格会被折叠 */}
                    {!compact && i < units.length - 1 ? "\u00A0" : null}
                  </motion.span>
                ))}
                {tail ? (
                  <motion.span
                    variants={riseChar}
                    className="seal-glow inline-block text-seal"
                    style={{ WebkitTextFillColor: "var(--seal)" }}
                  >
                    {tail}
                  </motion.span>
                ) : null}
              </motion.h1>
            </div>
          </div>

          {/* 工程主张作支撑句 */}
          <motion.p
            variants={fadeUp}
            className="mt-6 font-mono text-[13px] uppercase tracking-[0.16em] text-seal/90"
          >
            {t("title")} {t("titleAlt")}
            {/* 终端光标 —— 「The LLM writes the code」的常驻呼吸感 */}
            <span
              className="caret-blink ml-1.5 inline-block h-[1.05em] w-[0.55em] translate-y-[0.18em] bg-seal/60"
              aria-hidden
            />
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
