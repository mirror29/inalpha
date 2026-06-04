"use client";

import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ArrowDown, ArrowUp, Minus, TriangleAlert } from "lucide-react";
import useSWR from "swr";

import type {
  FactorEffectiveness,
  FactorSpec,
  FactorsPayload,
} from "@/lib/types";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip, Meta } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";

/** 因子目录静态、有效性手动触发,30s 一档够。 */
const REFRESH_MS = 30_000;

interface Target {
  venue: string;
  symbol: string;
  timeframe: string;
}
const DEFAULT_TARGET: Target = {
  venue: "binance",
  symbol: "BTC/USDT",
  timeframe: "1h",
};

export function FactorsClient() {
  const t = useTranslations("factors");
  const [target, setTarget] = useState<Target>(DEFAULT_TARGET);

  const key = `/api/factors?venue=${encodeURIComponent(target.venue)}&symbol=${encodeURIComponent(target.symbol)}&timeframe=${encodeURIComponent(target.timeframe)}`;
  const { data, error, isValidating, isLoading, mutate } =
    useSWR<FactorsPayload>(key, jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
        <SkeletonBlock className="h-72" />
        <SkeletonBlock className="h-96" />
      </div>
    );
  }
  if (error && !data) {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : String(error)}
        onRetry={() => mutate()}
      />
    );
  }
  if (!data) return null;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        index={t("index")}
        title={t("title")}
        subtitle={t("subtitle")}
        right={
          <LiveStrip
            asOf={data.asOf}
            isValidating={isValidating}
            isStaleFrame={Boolean(error)}
          >
            <Meta
              label={t("catalogCount")}
              value={String(data.catalog.length)}
              tone="muted"
            />
          </LiveStrip>
        }
      />

      {!data.catalogOk && (
        <SourceDownBanner text={t("serviceDown")} />
      )}

      {/* 系统全部因子目录在前(模块主体),针对标的的择时探针在后。 */}
      <CatalogPanel catalog={data.catalog} sources={data.sources} />

      <EffectivenessPanel
        eff={data.effectiveness}
        target={target}
        onApply={setTarget}
      />
    </div>
  );
}

// ── 有效因子择时 ──

function EffectivenessPanel({
  eff,
  target,
  onApply,
}: {
  eff: FactorsPayload["effectiveness"];
  target: Target;
  onApply: (t: Target) => void;
}) {
  const t = useTranslations("factors");
  const [form, setForm] = useState(target);

  // rank_ic 条形图按集合内最大 |ic| 相对缩放(至少 0.03 防止小值占满)。
  const maxIc = useMemo(() => {
    const m = Math.max(
      0.03,
      ...(eff?.top_factors ?? []).map((f) => Math.abs(f.rank_ic)),
    );
    return m;
  }, [eff]);

  return (
    <Panel
      index="5.2"
      title={t("effectiveness")}
      aside={
        <form
          className="flex flex-wrap items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            onApply(form);
          }}
        >
          <Input
            value={form.venue}
            onChange={(v) => setForm({ ...form, venue: v })}
            placeholder="venue"
            width="w-20"
          />
          <Input
            value={form.symbol}
            onChange={(v) => setForm({ ...form, symbol: v })}
            placeholder="symbol"
            width="w-28"
          />
          <Input
            value={form.timeframe}
            onChange={(v) => setForm({ ...form, timeframe: v })}
            placeholder="tf"
            width="w-14"
          />
          <button
            type="submit"
            className="rounded-md border border-cyan/40 bg-cyan/10 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-cyan transition-colors hover:bg-cyan/20"
          >
            {t("apply")}
          </button>
        </form>
      }
    >
      <p className="border-b border-border-subtle/60 px-4 py-2 text-[11px] text-fg-muted/70">
        {t("effectivenessHint")}
      </p>
      {!eff || !eff.available ? (
        <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
          <TriangleAlert className="size-6 text-gold/70" strokeWidth={1.5} />
          <p className="text-sm text-fg-muted">
            {eff?.reason ?? t("noEffectiveness")}
          </p>
          <p className="font-mono text-[11px] text-fg-muted/60">
            {target.symbol} · {target.venue} · {target.timeframe}
          </p>
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between border-b border-border-subtle/60 px-4 py-2 font-mono text-[11px] text-fg-muted">
            <span>
              {eff.symbol} · {eff.venue} · {eff.timeframe}
            </span>
            <span className="tnum">
              {t("barsUsed", { n: eff.bars_used })}
            </span>
          </div>
          <ul className="divide-y divide-border-subtle/40">
            {eff.top_factors.map((f) => (
              <FactorRow key={f.factor_id} f={f} maxIc={maxIc} />
            ))}
          </ul>
        </>
      )}
    </Panel>
  );
}

function FactorRow({ f, maxIc }: { f: FactorEffectiveness; maxIc: number }) {
  const locale = useLocale();
  const pct = Math.min(100, (Math.abs(f.rank_ic) / maxIc) * 100);
  const positive = f.rank_ic >= 0;

  return (
    <li className="flex items-center gap-3 px-4 py-2.5">
      {/* 名称 + kind */}
      <div className="w-44 shrink-0">
        <div className="truncate text-sm font-medium text-fg" title={f.factor_id}>
          {f.name}
        </div>
        <div className="font-mono text-[10px] uppercase tracking-wider text-fg-muted/60">
          {f.kind}
          {f.low_confidence && (
            <span className="ml-1 text-gold">· low-n</span>
          )}
        </div>
      </div>

      {/* 发散 rank_ic 条:中线两侧,绿正红负 */}
      <div className="relative h-4 flex-1">
        <span className="absolute left-1/2 top-0 h-full w-px bg-border-subtle" />
        <span
          className={cn(
            "absolute top-1/2 h-2.5 -translate-y-1/2 rounded-sm",
            positive ? "left-1/2 bg-bull/70" : "right-1/2 bg-fox-red/70",
          )}
          style={{ width: `${pct / 2}%` }}
        />
      </div>

      {/* 方向 + 数值 */}
      <div className="flex w-32 shrink-0 items-center justify-end gap-2 font-mono text-xs">
        <DirectionMark dir={f.direction} />
        <span
          className={cn(
            "tnum w-14 text-right",
            positive ? "text-bull" : "text-fox-red",
          )}
          title={`ICIR ${fmtNum(f.icir, locale, 2)}`}
        >
          {f.rank_ic >= 0 ? "+" : "−"}
          {fmtNum(Math.abs(f.rank_ic), locale, 3)}
        </span>
      </div>
    </li>
  );
}

function DirectionMark({ dir }: { dir: number }) {
  if (dir > 0)
    return <ArrowUp className="size-3.5 text-bull" strokeWidth={2.5} />;
  if (dir < 0)
    return <ArrowDown className="size-3.5 text-fox-red" strokeWidth={2.5} />;
  return <Minus className="size-3.5 text-fg-muted/50" strokeWidth={2.5} />;
}

// ── 因子目录 ──

function CatalogPanel({
  catalog,
  sources,
}: {
  catalog: FactorSpec[];
  sources: Record<string, boolean>;
}) {
  const t = useTranslations("factors");
  const [kind, setKind] = useState<string>("all");

  const kinds = useMemo(
    () => Array.from(new Set(catalog.map((f) => f.kind))).sort(),
    [catalog],
  );
  const shown = kind === "all" ? catalog : catalog.filter((f) => f.kind === kind);

  // 按 source 分组
  const bySource = useMemo(() => {
    const m = new Map<string, FactorSpec[]>();
    for (const f of shown) {
      const arr = m.get(f.source) ?? [];
      arr.push(f);
      m.set(f.source, arr);
    }
    return [...m.entries()];
  }, [shown]);

  return (
    <Panel
      index="5.1"
      title={t("catalog")}
      aside={
        <div className="flex flex-wrap items-center gap-1">
          <span className="mr-1.5 whitespace-nowrap font-mono text-[10px] uppercase tracking-wider text-fg-muted/70">
            {t("totalFactors", { n: catalog.length })}
          </span>
          <Chip label={t("filter.all")} active={kind === "all"} onClick={() => setKind("all")} />
          {kinds.map((k) => (
            <Chip key={k} label={k} active={kind === k} onClick={() => setKind(k)} />
          ))}
        </div>
      }
    >
      <p className="border-b border-border-subtle/60 px-4 py-2 text-[11px] text-fg-muted/70">
        {t("catalogHint")}
      </p>
      <div className="flex flex-col gap-4 p-4">
        {bySource.map(([source, factors]) => (
          <div key={source}>
            <div className="mb-2 flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider">
              <span className="text-cyan">{source}</span>
              <span className="text-fg-muted/60">{factors.length}</span>
              {sources[source] === false && (
                <span className="rounded border border-border-subtle px-1 text-[9px] text-fg-muted/60">
                  off
                </span>
              )}
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {factors.map((f) => (
                <FactorCard key={f.factor_id} f={f} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function FactorCard({ f }: { f: FactorSpec }) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border-subtle bg-bg-elev/20 px-3 py-2",
        !f.available && "opacity-45",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium text-fg">{f.name}</span>
        <DirectionMark dir={f.direction_hint} />
      </div>
      <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-fg-muted/60">
        <span className="uppercase tracking-wider">{f.kind}</span>
        {f.needs_universe && <span className="text-gold">· universe</span>}
        {!f.available && <span className="text-fox-red">· off</span>}
      </div>
    </div>
  );
}

// ── 小组件 ──

function Input({
  value,
  onChange,
  placeholder,
  width,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  width: string;
}) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      spellCheck={false}
      className={cn(
        "rounded-md border border-border-subtle bg-bg-deep/40 px-2 py-1 font-mono text-[11px] text-fg placeholder:text-fg-muted/40 focus:border-cyan/50 focus:outline-none",
        width,
      )}
    />
  );
}

function Chip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider transition-colors",
        active
          ? "border-cyan/40 bg-cyan/10 text-cyan"
          : "border-border-subtle text-fg-muted hover:text-fg",
      )}
    >
      {label}
    </button>
  );
}

function SourceDownBanner({ text }: { text: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-gold/30 bg-gold/[0.07] px-4 py-2.5">
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-gold" strokeWidth={2} />
      <p className="text-sm text-fg-muted">{text}</p>
    </div>
  );
}
