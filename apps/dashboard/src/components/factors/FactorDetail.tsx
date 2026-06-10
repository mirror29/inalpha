"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { useLocale, useTranslations } from "next-intl";
import { ArrowDown, ArrowUp, Minus, X } from "lucide-react";

import type { FactorEffectiveness } from "@/lib/types";
import { cn } from "@/lib/cn";
import { factorDescription } from "@/lib/factor-info";
import { fmtNum } from "@/lib/format";

/** 衰减状态(由近期 IC vs 全样本 IC 判定)。 */
export type DecayState = "stable" | "fading" | "decaying";

/**
 * 衰减判定 —— 近期 IC(近 1/3 样本)对全样本 IC:
 * 反号 / 趋零 = 衰减;量级保住 60% 以上 = 稳定;其间 = 走弱。
 * 阈值与 factor 服务 rank_ic_recent 的语义(schemas.FactorEffectiveness)对齐。
 */
export function decayState(f: FactorEffectiveness): DecayState {
  const ic = f.rank_ic;
  const recent = f.rank_ic_recent ?? 0;
  if (recent === 0 || Math.sign(recent) !== Math.sign(ic)) return "decaying";
  return Math.abs(recent) >= 0.6 * Math.abs(ic) ? "stable" : "fading";
}

const DECAY_CLS: Record<DecayState, string> = {
  stable: "border-bull/35 text-bull",
  fading: "border-gold/40 text-gold",
  decaying: "border-fox-red/40 text-fox-red",
};

export function DecayBadge({ state }: { state: DecayState }) {
  const t = useTranslations("runners.factors");
  return (
    <span
      className={cn(
        "w-14 shrink-0 rounded border px-1 py-0.5 text-center font-mono text-[10px] uppercase tracking-wider",
        DECAY_CLS[state],
      )}
    >
      {t(state)}
    </span>
  );
}

/** 详情弹层的目标因子 —— catalog(静态)与 runner 有效性行(带实测指标)共用。 */
export interface FactorDetailTarget {
  factor_id: string;
  name: string;
  kind: string;
  source?: string;
  /** 方向先验(catalog 的 direction_hint)或实测方向(eff.direction)。 */
  direction?: number;
  needsUniverse?: boolean;
  /** 运行时实测有效性 —— 模拟盘入口才有,catalog 入口为空。 */
  eff?: FactorEffectiveness;
}

/**
 * 因子详情弹层 —— 名称只有一行标题不够用:这里给「度量什么 + 怎么读」的说明
 * (lib/factor-info 字典),模拟盘入口再附当前标的的实测有效性与指标图例。
 */
export function FactorDetailOverlay({
  target,
  onClose,
}: {
  target: FactorDetailTarget | null;
  onClose: () => void;
}) {
  const t = useTranslations("factors.detail");
  const locale = useLocale();

  // Escape 关闭 —— 仅弹层打开时挂监听。
  useEffect(() => {
    if (!target) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [target, onClose]);

  if (!target) return null;
  const desc = factorDescription(target.factor_id, locale);
  const eff = target.eff;

  // portal 挂 body —— Panel 有 backdrop-blur(backdrop-filter 会让 fixed 以它为
  // containing block),不出去弹层会被困在面板内/被 overflow 裁掉。
  // 弹层只在用户点击后渲染(纯客户端),document 必然可用。
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-label={target.name}
    >
      {/* 背景遮罩 —— 点击关闭 */}
      <button
        type="button"
        aria-label={t("close")}
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-bg-deep/70 backdrop-blur-sm"
      />
      <div className="relative w-full max-w-lg overflow-hidden rounded-xl border border-border-subtle bg-bg-elev shadow-2xl">
        {/* 头部:名称 + factor_id + 关闭 */}
        <div className="flex items-start justify-between gap-3 border-b border-border-subtle px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-base text-fg">{target.name}</h2>
            <div className="mt-0.5 truncate font-mono text-[11px] text-fg-muted/70">
              {target.factor_id}
            </div>
          </div>
          <button
            type="button"
            aria-label={t("close")}
            onClick={onClose}
            className="rounded-md border border-border-subtle p-1 text-fg-muted transition-colors hover:border-cyan/40 hover:text-cyan"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex flex-col gap-4 px-5 py-4">
          {/* 静态属性徽章 */}
          <div className="flex flex-wrap items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider">
            {target.source && <Tag label={`${t("source")} ${target.source}`} />}
            <Tag label={`${t("kind")} ${target.kind}`} />
            <DirectionTag dir={target.direction ?? 0} measured={Boolean(eff)} />
          </div>
          {target.needsUniverse && (
            <p className="text-[11px] text-gold/90">{t("universe")}</p>
          )}

          {/* 因子说明 */}
          <p className="text-sm leading-relaxed text-fg-muted">
            {desc ?? t("noDescription")}
          </p>

          {/* 实测有效性(模拟盘入口) */}
          {eff && (
            <div className="rounded-lg border border-border-subtle/70 bg-bg-deep/30 p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-fg-muted">
                  {t("effTitle")}
                </span>
                <DecayBadge state={decayState(eff)} />
              </div>
              <dl className="grid grid-cols-3 gap-x-3 gap-y-2">
                <Metric label={t("mRankIc")} value={fmtSignedNum(eff.rank_ic, locale, 3)} colored={eff.rank_ic} />
                <Metric label={t("mRecent")} value={fmtSignedNum(eff.rank_ic_recent ?? 0, locale, 3)} colored={eff.rank_ic_recent ?? 0} />
                <Metric label="ICIR" value={fmtSignedNum(eff.icir, locale, 2)} />
                <Metric label={t("mTurnover")} value={fmtNum(eff.turnover ?? 0, locale, 2)} />
                <Metric label={t("mSample")} value={String(eff.sample_size)} />
                <Metric label={t("mLs")} value={fmtSignedNum(eff.long_short_return, locale, 4)} colored={eff.long_short_return} />
              </dl>
              {eff.low_confidence && (
                <p className="mt-2 text-[11px] text-gold">{t("lowConfidence")}</p>
              )}
              <p className="mt-2 border-t border-border-subtle/50 pt-2 text-[11px] leading-relaxed text-fg-muted/70">
                {t("legend")}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function Tag({ label }: { label: string }) {
  return (
    <span className="rounded border border-border-subtle px-1.5 py-0.5 text-fg-muted">
      {label}
    </span>
  );
}

/** 方向标签 —— catalog 显先验,eff 入口显实测方向。 */
function DirectionTag({ dir, measured }: { dir: number; measured: boolean }) {
  const t = useTranslations("factors.detail");
  const label = measured ? t("dirMeasured") : t("dirPrior");
  const Icon = dir > 0 ? ArrowUp : dir < 0 ? ArrowDown : Minus;
  const cls = dir > 0 ? "text-bull" : dir < 0 ? "text-fox-red" : "text-fg-muted/60";
  return (
    <span className="flex items-center gap-1 rounded border border-border-subtle px-1.5 py-0.5 text-fg-muted">
      {label}
      <Icon className={cn("size-3", cls)} strokeWidth={2.5} />
    </span>
  );
}

function Metric({
  label,
  value,
  colored,
}: {
  label: string;
  value: string;
  colored?: number;
}) {
  return (
    <div>
      <dt className="font-mono text-[9px] uppercase tracking-[0.14em] text-fg-muted/60">
        {label}
      </dt>
      <dd
        className={cn(
          "tnum mt-0.5 font-mono text-sm",
          colored === undefined
            ? "text-fg"
            : colored >= 0
              ? "text-bull"
              : "text-fox-red",
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function fmtSignedNum(v: number, locale: string, digits: number): string {
  return `${v >= 0 ? "+" : "−"}${fmtNum(Math.abs(v), locale, digits)}`;
}
