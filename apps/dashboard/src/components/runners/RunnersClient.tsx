"use client";

import { useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { Radio } from "lucide-react";
import useSWR from "swr";

import type { RunnersPayload, StrategyRunRecord } from "@/lib/types";
import { cn } from "@/lib/cn";
import { jsonFetcher } from "@/lib/fetcher";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { LiveStrip, Meta } from "@/components/ui/LiveStrip";
import { PageHeader } from "@/components/ui/PageHeader";
import { TruncationNote } from "@/components/ui/TruncationNote";
import { RunnerCard } from "./RunnerCard";

/** live runner 贴近 bar 周期,6s 一档。 */
const REFRESH_MS = 6000;

type StatusFilter = "all" | "running" | "stopped" | "errored";
const STATUSES: StatusFilter[] = ["all", "running", "stopped", "errored"];

export function RunnersClient() {
  const t = useTranslations("runners");
  const tc = useTranslations("common");
  const [filter, setFilter] = useState<StatusFilter>("all");

  const { data, error, isValidating, isLoading, mutate } =
    useSWR<RunnersPayload>("/api/runners", jsonFetcher, {
      refreshInterval: REFRESH_MS,
      keepPreviousData: true,
    });

  // 按策略(candidate)分组:重启会新建 run 记录(审计保留),平铺会让一个策略
  // 重启 N 次长出 N 张并排卡片、看起来像 N 个策略。每组立最新 run 的卡,
  // 更早的 run 作为 history 折叠进卡底。
  const groups = useMemo(() => {
    const byCandidate = new Map<string, StrategyRunRecord[]>();
    for (const r of data?.runs ?? []) {
      const list = byCandidate.get(r.candidate_id);
      if (list) list.push(r);
      else byCandidate.set(r.candidate_id, [r]);
    }
    const out = [...byCandidate.values()].map((rs) => {
      const sorted = [...rs].sort((a, b) =>
        b.started_at.localeCompare(a.started_at),
      );
      return { latest: sorted[0], history: sorted.slice(1) };
    });
    // 运行中的策略排前,同状态按最新启动时间倒序。
    out.sort((a, b) => {
      const ar = a.latest.status === "running" ? 0 : 1;
      const br = b.latest.status === "running" ? 0 : 1;
      if (ar !== br) return ar - br;
      return b.latest.started_at.localeCompare(a.latest.started_at);
    });
    return out;
  }, [data]);

  // 各状态计数(chip 角标)—— 按「策略组的最新 run 状态」计,与卡片一一对应。
  const counts = useMemo(() => {
    const c = { all: 0, running: 0, stopped: 0, errored: 0 };
    c.all = groups.length;
    for (const g of groups) c[g.latest.status] += 1;
    return c;
  }, [groups]);

  const filtered = useMemo(
    () =>
      filter === "all"
        ? groups
        : groups.filter((g) => g.latest.status === filter),
    [groups, filter],
  );

  if (isLoading && !data) {
    return (
      <div className="flex flex-col gap-6">
        <SkeletonBlock className="h-16 w-72 border-0 bg-bg-elev/40" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonBlock key={i} className="h-48" />
          ))}
        </div>
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
              label={t("running")}
              value={`${data.runningCount}/${data.runs.length}`}
              tone={data.runningCount > 0 ? "bull" : "muted"}
            />
          </LiveStrip>
        }
      />

      {/* 状态筛选 chip */}
      <div className="flex flex-wrap gap-1.5">
        {STATUSES.map((s) => (
          <FilterChip
            key={s}
            label={`${t(`filter.${s}`)} ${counts[s]}`}
            active={filter === s}
            onClick={() => setFilter(s)}
          />
        ))}
      </div>

      {filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border-subtle py-20 text-center">
          <Radio className="size-7 text-fg-muted/50" strokeWidth={1.5} />
          <p className="max-w-md text-sm text-fg-muted">
            {data.runs.length === 0
              ? t("empty")
              : t("emptyFiltered", { status: t(`filter.${filter}`) })}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((g) => (
            <RunnerCard key={g.latest.id} run={g.latest} history={g.history} />
          ))}
        </div>
      )}

      {data.truncated && (
        <TruncationNote text={tc("truncated", { n: data.runs.length })} />
      )}
    </div>
  );
}

function FilterChip({
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
        "rounded-md border px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider transition-colors",
        active
          ? "border-cyan/40 bg-cyan/10 text-cyan"
          : "border-border-subtle text-fg-muted hover:text-fg",
      )}
    >
      {label}
    </button>
  );
}
