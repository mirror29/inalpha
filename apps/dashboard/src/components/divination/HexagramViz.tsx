"use client";

import { useTranslations } from "next-intl";

import { cn } from "@/lib/cn";
import type { HexagramInfoView, HexagramLineView } from "./types";

/**
 * 六爻卦象 SVG —— 自下而上六条爻线。
 *
 * 阳爻(—)画整条；阴爻(--)画中断两段；动爻(老阴 6 / 老阳 9)用朱红高亮 + 右侧标记。
 * 纯展示组件,数据来自 `divination.cast_hexagram` 的返回。
 */
function HexagramLines({ lines }: { lines: HexagramLineView[] }) {
  // 自上而下渲染:上爻(position 6)在最上面 → 逆序。
  const ordered = [...lines].sort((a, b) => b.position - a.position);
  const rowH = 22;
  const barW = 116;
  const gap = 16; // 阴爻中断缺口
  const height = ordered.length * rowH;

  return (
    <svg
      viewBox={`0 0 140 ${height}`}
      width="140"
      height={height}
      role="img"
      aria-label="hexagram"
      className="shrink-0"
    >
      {ordered.map((line, i) => {
        const y = i * rowH + rowH / 2;
        const stroke = line.changing ? "var(--seal)" : "var(--ink)";
        const sw = 7;
        return (
          <g key={line.position}>
            {line.yang ? (
              <line x1={12} y1={y} x2={12 + barW} y2={y} stroke={stroke} strokeWidth={sw} strokeLinecap="round" />
            ) : (
              <>
                <line x1={12} y1={y} x2={12 + (barW - gap) / 2} y2={y} stroke={stroke} strokeWidth={sw} strokeLinecap="round" />
                <line x1={12 + (barW + gap) / 2} y1={y} x2={12 + barW} y2={y} stroke={stroke} strokeWidth={sw} strokeLinecap="round" />
              </>
            )}
            {line.changing && (
              <circle cx={134} cy={y} r={3} fill="var(--seal)" />
            )}
          </g>
        );
      })}
    </svg>
  );
}

/** 单卦面板：卦名 + 卦象 + 卦辞。`variant` 区分本卦 / 变卦配色。 */
function HexagramPanel({
  info,
  lines,
  label,
  variant,
}: {
  info: HexagramInfoView;
  lines?: HexagramLineView[];
  label: string;
  variant: "primary" | "changed";
}) {
  return (
    <div className="flex flex-col items-center gap-2">
      <span
        className={cn(
          "font-mono text-[10px] uppercase tracking-[0.16em]",
          variant === "primary" ? "text-cyan" : "text-gold",
        )}
      >
        {label}
      </span>
      {lines ? (
        <HexagramLines lines={lines} />
      ) : (
        // 变卦只有静态卦,用本卦缺省渲染:按 binary 构造仅阴阳的爻
        <HexagramLines
          lines={info.binary
            .split("")
            .map((b, i) => ({
              position: i + 1,
              value: (b === "1" ? 7 : 8) as 6 | 7 | 8 | 9,
              yang: b === "1",
              changing: false,
            }))}
        />
      )}
      <div className="text-center">
        <div className="font-display text-2xl leading-tight text-fg">
          {info.name}
          <span className="ml-1.5 font-mono text-xs text-fg-muted">#{info.number}</span>
        </div>
        <div className="font-mono text-[10px] text-fg-muted/80">{info.english}</div>
      </div>
      <p className="max-w-[15rem] text-center text-xs leading-relaxed text-fg-muted">
        {info.judgment}
      </p>
    </div>
  );
}

/**
 * 六爻卦象可视化 —— 本卦 + (有动爻时)变卦并排,下方列动爻。
 *
 * @param reading `divination.cast_hexagram` 的返回结构
 */
export function HexagramViz({
  primary,
  changed,
  changingLines,
}: {
  primary: HexagramInfoView & { lines: HexagramLineView[] };
  changed: HexagramInfoView | null;
  changingLines: number[];
}) {
  const t = useTranslations("divination");

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-start justify-center gap-x-10 gap-y-4">
        <HexagramPanel
          info={primary}
          lines={primary.lines}
          label={t("primaryHexagram")}
          variant="primary"
        />
        {changed && (
          <>
            <div className="flex items-center self-center font-mono text-2xl text-fg-muted/40">→</div>
            <HexagramPanel info={changed} label={t("changedHexagram")} variant="changed" />
          </>
        )}
      </div>
      <p className="text-center font-mono text-[11px] text-fg-muted">
        {changingLines.length > 0
          ? t("changingLines", { lines: changingLines.join("、") })
          : t("noChangingLines")}
      </p>
    </div>
  );
}
