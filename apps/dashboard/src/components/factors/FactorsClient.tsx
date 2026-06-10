"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { ArrowDown, ArrowUp, Minus, TriangleAlert } from "lucide-react";
import useSWR from "swr";

import type { FactorSpec, FactorsPayload } from "@/lib/types";
import { cn } from "@/lib/cn";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip, Meta } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { FactorDetailOverlay } from "./FactorDetail";

/** 纯 catalog 请求(毫秒级,无快照计算),静态目录 60s 一刷足够。 */
const REFRESH_MS = 60_000;

/**
 * 因子库页 —— 系统全量因子目录,点卡片看因子详情(度量什么 / 怎么读)。
 * 针对标的的择时有效性已移到模拟盘详情页(RunnerFactors),那里有真实的
 * venue/symbol/timeframe 上下文,不用在这手填标的。
 */
export function FactorsClient() {
  const t = useTranslations("factors");

  const { data, error, isValidating, isLoading, mutate } =
    useSWR<FactorsPayload>("/api/factors", jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
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

      {!data.catalogOk && <SourceDownBanner text={t("serviceDown")} />}

      <CatalogPanel catalog={data.catalog} sources={data.sources} />
    </div>
  );
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
  const [detail, setDetail] = useState<FactorSpec | null>(null);

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
                <FactorCard key={f.factor_id} f={f} onOpen={() => setDetail(f)} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <FactorDetailOverlay
        target={
          detail
            ? {
                factor_id: detail.factor_id,
                name: detail.name,
                kind: detail.kind,
                source: detail.source,
                direction: detail.direction_hint,
                needsUniverse: detail.needs_universe,
              }
            : null
        }
        onClose={() => setDetail(null)}
      />
    </Panel>
  );
}

function FactorCard({ f, onOpen }: { f: FactorSpec; onOpen: () => void }) {
  const t = useTranslations("factors");
  return (
    <button
      type="button"
      onClick={onOpen}
      title={t("viewDetail")}
      className={cn(
        "rounded-lg border border-border-subtle bg-bg-elev/20 px-3 py-2 text-left transition-colors hover:border-cyan/40 hover:bg-bg-elev/40",
        !f.available && "opacity-45",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm text-fg">{f.name}</span>
        <DirectionMark dir={f.direction_hint} />
      </div>
      <div className="mt-0.5 flex items-center gap-1.5 font-mono text-[10px] text-fg-muted/60">
        <span className="uppercase tracking-wider">{f.kind}</span>
        {f.needs_universe && <span className="text-gold">· universe</span>}
        {!f.available && <span className="text-fox-red">· off</span>}
      </div>
    </button>
  );
}

function DirectionMark({ dir }: { dir: number }) {
  if (dir > 0)
    return <ArrowUp className="size-3.5 text-bull" strokeWidth={2.5} />;
  if (dir < 0)
    return <ArrowDown className="size-3.5 text-fox-red" strokeWidth={2.5} />;
  return <Minus className="size-3.5 text-fg-muted/50" strokeWidth={2.5} />;
}

// ── 小组件 ──

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
