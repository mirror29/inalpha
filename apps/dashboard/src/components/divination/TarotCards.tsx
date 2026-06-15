"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/cn";
import type { DrawnCardView } from "./types";

/**
 * 花色 → 符号 + 主题色 + 辉光色 + 标签。用于无图回退的 SVG 法阵卡;有图时主用真实牌面。
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

/** 大牌英文名 → 牌号(与 public/tarot/major-NN.jpg 对应)。 */
const MAJOR_NUMBER: Record<string, number> = {
  "The Fool": 0,
  "The Magician": 1,
  "The High Priestess": 2,
  "The Empress": 3,
  "The Emperor": 4,
  "The Hierophant": 5,
  "The Lovers": 6,
  "The Chariot": 7,
  Strength: 8,
  "The Hermit": 9,
  "Wheel of Fortune": 10,
  Justice: 11,
  "The Hanged Man": 12,
  Death: 13,
  Temperance: 14,
  "The Devil": 15,
  "The Tower": 16,
  "The Star": 17,
  "The Moon": 18,
  "The Sun": 19,
  Judgement: 20,
  "The World": 21,
};

/** 小牌点数词 → 序号(Ace=1 .. Page=11 Knight=12 Queen=13 King=14)。 */
const RANK_NUMBER: Record<string, number> = {
  Ace: 1,
  Two: 2,
  Three: 3,
  Four: 4,
  Five: 5,
  Six: 6,
  Seven: 7,
  Eight: 8,
  Nine: 9,
  Ten: 10,
  Page: 11,
  Knight: 12,
  Queen: 13,
  King: 14,
};

/**
 * 牌 → public/tarot/ 下的图片 key(major-NN / <suit>-NN);映射不出返回 null,
 * 走 SVG 法阵卡回退。key 由 fetch-tarot.sh 落盘的命名约定保证一致。
 */
function cardKey(card: DrawnCardView): string | null {
  if (card.arcana === "major") {
    const n = MAJOR_NUMBER[card.english];
    return n === undefined ? null : `major-${String(n).padStart(2, "0")}`;
  }
  const rank = RANK_NUMBER[card.english.split(" of ")[0]];
  return rank === undefined ? null : `${card.arcana}-${String(rank).padStart(2, "0")}`;
}

/** 法阵纹章(无图回退用):双环 + 四方位菱形 + 12 放射刻度,色随 currentColor。 */
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

/** 无图回退:SVG 法阵卡(花色标签 + 纹章 + 名字 cartouche)。 */
function SigilFace({ card }: { card: DrawnCardView }) {
  const meta = ARCANA_META[card.arcana];
  return (
    <div
      className="absolute inset-0 flex flex-col items-center justify-between px-2.5 py-3.5"
      style={{
        backgroundImage: `radial-gradient(circle at 50% 34%, color-mix(in oklab, ${meta.glow} 16%, transparent), transparent 68%)`,
      }}
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
  );
}

/** 单张塔罗牌:优先真实牌图,加载失败 / 无映射回退 SVG 法阵卡。逆位整张 180° 倒转。 */
function TarotCard({ card }: { card: DrawnCardView }) {
  const t = useTranslations("divination");
  const [imgError, setImgError] = useState(false);
  const key = cardKey(card);
  const showImg = key !== null && !imgError;
  const keywords = card.isReversed ? card.reversed : card.upright;

  return (
    <div className="flex w-36 flex-col items-center gap-2">
      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted/70">
        {t(POSITION_KEY[card.position])}
      </span>

      <div
        className={cn(
          // 无图时给固定塔罗比例撑高;有图时由图自身高度决定。
          "relative w-full overflow-hidden rounded-lg border bg-bg-deep/60 shadow-lg",
          !showImg && "aspect-[9/14]",
          card.isReversed ? "border-seal/45" : "border-border-subtle",
        )}
      >
        {/* 逆位角标(不随牌面旋转) */}
        {card.isReversed && (
          <span className="absolute right-1.5 top-1.5 z-10 rounded-sm border border-seal/40 bg-bg-deep/70 px-1 py-px font-mono text-[8px] uppercase tracking-wider text-seal">
            {t("reversed")}
          </span>
        )}

        {showImg ? (
          // eslint-disable-next-line @next/next/no-img-element -- 本地 public 静态图,各牌宽度不一,不用 next/image 固定尺寸
          <img
            src={`/tarot/${key}.jpg`}
            alt={card.english}
            loading="lazy"
            onError={() => setImgError(true)}
            className={cn("block w-full transition-transform", card.isReversed && "rotate-180")}
          />
        ) : (
          <SigilFace card={card} />
        )}
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
