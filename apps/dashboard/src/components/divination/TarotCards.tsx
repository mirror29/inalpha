"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/cn";
import type { DrawnCardView } from "./types";

/**
 * 花色 → 符号 + 主题色（文字 class 驱动 currentColor，纹章 / 辉光统一取它）+ 辉光用的
 * CSS 变量（radial-gradient 需要真实色值，不能用 tailwind class）。
 */
const ARCANA_META: Record<
  DrawnCardView["arcana"],
  { glyph: string; accent: string; glow: string; label: string }
> = {
  major: { glyph: "✦", accent: "text-seal", glow: "var(--seal)", label: "MAJOR" },
  wands: { glyph: "♣", accent: "text-gold", glow: "var(--gold)", label: "WANDS" },
  cups: { glyph: "♥", accent: "text-cyan", glow: "var(--accent)", label: "CUPS" },
  swords: { glyph: "♠", accent: "text-fg", glow: "var(--fg-muted)", label: "SWORDS" },
  pentacles: { glyph: "◈", accent: "text-bull", glow: "var(--up)", label: "PENT" },
};

/** 牌阵位置 → i18n key。 */
const POSITION_KEY: Record<DrawnCardView["position"], string> = {
  single: "posPresent",
  past: "posPast",
  present: "posPresent",
  future: "posFuture",
};

/** 法阵纹章：双环 + 四方位菱形 + 12 放射刻度,色随 currentColor。纯装饰。 */
function ArcaneSigil({ glyph, accent }: { glyph: string; accent: string }) {
  const ticks = Array.from({ length: 12 }, (_, i) => i * 30);
  const cardinals = [0, 90, 180, 270];
  const rad = (deg: number) => (deg * Math.PI) / 180;
  return (
    <div className={cn("relative size-[68px]", accent)}>
      <svg
        viewBox="0 0 100 100"
        className="absolute inset-0 size-full"
        fill="none"
        stroke="currentColor"
        aria-hidden
      >
        <circle cx="50" cy="50" r="44" strokeWidth="1.2" opacity="0.9" />
        <circle cx="50" cy="50" r="35" strokeWidth="0.8" opacity="0.35" />
        {ticks.map((deg) => {
          const c = Math.cos(rad(deg));
          const s = Math.sin(rad(deg));
          return (
            <line
              key={deg}
              x1={50 + 39 * c}
              y1={50 + 39 * s}
              x2={50 + 44 * c}
              y2={50 + 44 * s}
              strokeWidth="0.8"
              opacity="0.5"
            />
          );
        })}
        {cardinals.map((deg) => {
          const c = Math.cos(rad(deg));
          const s = Math.sin(rad(deg));
          return (
            <rect
              key={deg}
              x={50 + 44 * c - 2.4}
              y={50 + 44 * s - 2.4}
              width="4.8"
              height="4.8"
              transform={`rotate(45 ${50 + 44 * c} ${50 + 44 * s})`}
              fill="currentColor"
              stroke="none"
              opacity="0.85"
            />
          );
        })}
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-3xl">
        {glyph}
      </span>
    </div>
  );
}

/** 单张塔罗牌：法阵纹章 + 名字 cartouche + 正逆位 + 关键词。逆位整张牌面旋转 180°。 */
function TarotCard({ card }: { card: DrawnCardView }) {
  const t = useTranslations("divination");
  const meta = ARCANA_META[card.arcana];
  const keywords = card.isReversed ? card.reversed : card.upright;

  return (
    <div className="flex w-36 flex-col items-center gap-2">
      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted/70">
        {t(POSITION_KEY[card.position])}
      </span>

      <div
        className={cn(
          "relative aspect-[9/14] w-full overflow-hidden rounded-lg border bg-bg-deep/60 shadow-lg transition-colors",
          card.isReversed ? "border-seal/45" : "border-border-subtle",
        )}
        style={{
          backgroundImage: `radial-gradient(circle at 50% 34%, color-mix(in oklab, ${meta.glow} 16%, transparent), transparent 68%)`,
        }}
      >
        {/* 内嵌描边卡框 + 四角点缀 */}
        <div
          className={cn(
            "pointer-events-none absolute inset-[5px] rounded-md border",
            card.isReversed ? "border-seal/25" : "border-border-subtle/50",
          )}
        />

        {/* 逆位角标(不随牌面旋转) */}
        {card.isReversed && (
          <span className="absolute right-1.5 top-1.5 z-10 rounded-sm border border-seal/40 bg-bg-deep/70 px-1 py-px font-mono text-[8px] uppercase tracking-wider text-seal">
            {t("reversed")}
          </span>
        )}

        {/* 牌面主体:逆位时整体旋转 180° */}
        <div
          className={cn(
            "absolute inset-0 flex flex-col items-center justify-between px-2.5 py-3.5 transition-transform",
            card.isReversed && "rotate-180",
          )}
        >
          <span className={cn("font-mono text-[8px] uppercase tracking-[0.22em]", meta.accent)}>
            {meta.label}
          </span>

          <ArcaneSigil glyph={meta.glyph} accent={meta.accent} />

          <div className="w-full text-center">
            <div className="mx-auto mb-1 h-px w-8 bg-border-subtle" />
            <div className="font-display text-[15px] leading-tight text-fg">{card.name}</div>
            <div className="font-mono text-[8px] uppercase tracking-wider text-fg-muted/60">
              {card.english}
            </div>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap justify-center gap-1">
        {keywords.map((kw) => (
          <span
            key={kw}
            className="rounded-sm border border-border-subtle/70 px-1.5 py-px font-mono text-[10px] text-fg-muted"
          >
            {kw}
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * 塔罗牌阵可视化 —— 单张居中,三张横排(过去 / 现在 / 未来);窄栏自动换行。
 *
 * @param cards `divination.draw_tarot` 返回的牌数组
 */
export function TarotCards({ cards }: { cards: DrawnCardView[] }) {
  return (
    <div className="flex flex-wrap items-start justify-center gap-3">
      {cards.map((card, i) => (
        <TarotCard key={`${card.english}-${i}`} card={card} />
      ))}
    </div>
  );
}
