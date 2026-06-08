"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/cn";
import type { DrawnCardView } from "./types";

/** 花色 → 朱红 / 青 / 金 等点缀色 + 符号(纯装饰)。 */
const ARCANA_META: Record<
  DrawnCardView["arcana"],
  { glyph: string; accent: string }
> = {
  major: { glyph: "✦", accent: "text-seal" },
  wands: { glyph: "♣", accent: "text-gold" },
  cups: { glyph: "♥", accent: "text-cyan" },
  swords: { glyph: "♠", accent: "text-fg" },
  pentacles: { glyph: "◈", accent: "text-bull" },
};

/** 牌阵位置 → i18n key。 */
const POSITION_KEY: Record<DrawnCardView["position"], string> = {
  single: "posPresent",
  past: "posPast",
  present: "posPresent",
  future: "posFuture",
};

/** 单张塔罗牌：牌面 + 正逆位 + 关键词。逆位时整张牌旋转 180°。 */
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
          "relative flex h-52 w-full flex-col items-center justify-between rounded-lg border bg-bg-deep/50 p-3 transition-colors",
          card.isReversed ? "border-seal/40" : "border-border-subtle",
        )}
      >
        {/* 逆位角标 */}
        {card.isReversed && (
          <span className="absolute right-1.5 top-1.5 font-mono text-[9px] uppercase tracking-wider text-seal">
            {t("reversed")}
          </span>
        )}
        <div
          className={cn(
            "flex flex-1 flex-col items-center justify-center gap-2 transition-transform",
            card.isReversed && "rotate-180",
          )}
        >
          <span className={cn("text-4xl", meta.accent)}>{meta.glyph}</span>
          <div className="text-center">
            <div className="font-display text-base leading-tight text-fg">{card.name}</div>
            <div className="font-mono text-[9px] text-fg-muted/70">{card.english}</div>
          </div>
        </div>
      </div>
      <div className="flex flex-wrap justify-center gap-1">
        {keywords.map((kw) => (
          <span
            key={kw}
            className="rounded-sm border border-border-subtle px-1.5 py-px font-mono text-[10px] text-fg-muted"
          >
            {kw}
          </span>
        ))}
      </div>
    </div>
  );
}

/**
 * 塔罗牌阵可视化 —— 单张居中,三张横排(过去 / 现在 / 未来)。
 *
 * @param cards `divination.draw_tarot` 返回的牌数组
 */
export function TarotCards({ cards }: { cards: DrawnCardView[] }) {
  return (
    <div className="flex flex-wrap items-start justify-center gap-4">
      {cards.map((card, i) => (
        <TarotCard key={`${card.english}-${i}`} card={card} />
      ))}
    </div>
  );
}
