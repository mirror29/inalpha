"use client";

import { useState } from "react";
import { useLocale } from "next-intl";
import { CheckCircle2, CircleDashed, XCircle } from "lucide-react";
import useSWR from "swr";

import type { FactorCandidate } from "@/lib/types";
import { cn } from "@/lib/cn";
import { jsonFetcher } from "@/lib/fetcher";
import { Panel } from "@/components/ui/Panel";

const REFRESH_MS = 30_000;

/**
 * 文案走本地 zh/en 字典(同 lib/factor-info.ts 模式),不进 messages JSON ——
 * 该 JSON 当前有并行改动,候选区块字符串自包含,避免跨模块卷动。
 */
const STR = {
  title: { zh: "因子候选池", en: "Factor candidates" },
  hint: {
    zh: "agent 提出的自定义因子表达式。register 后即进目录成为生产因子(custom 源);这一步只能在这里人工完成,agent 没有转正工具。",
    en: "Custom factor expressions proposed by agents. Registering puts them into the catalog as production factors (custom source); only a human can do this here — agents have no register tool.",
  },
  unavailable: {
    zh: "候选池不可用(factor 服务未连接数据库)。",
    en: "Candidate pool unavailable (factor service has no DB connection).",
  },
  empty: { zh: "暂无候选。", en: "No candidates yet." },
  register: { zh: "注册", en: "Register" },
  reject: { zh: "拒绝", en: "Reject" },
  reviewFailed: {
    zh: "操作失败：{msg}",
    en: "Action failed: {msg}",
  },
  pending: { zh: "待审核", en: "Pending" },
  registered: { zh: "已注册", en: "Registered" },
  rejected: { zh: "已拒绝", en: "Rejected" },
  hypothesis: { zh: "假设", en: "Hypothesis" },
  nTested: { zh: "选自 {n} 次尝试", en: "from {n} attempts" },
  ctx: { zh: "评估上下文", en: "Evaluated on" },
} as const;

type StrKey = keyof typeof STR;

function useStr() {
  const locale = useLocale();
  const zh = locale.startsWith("zh");
  return (k: StrKey, vars?: Record<string, string | number>) => {
    let s: string = zh ? STR[k].zh : STR[k].en;
    for (const [key, v] of Object.entries(vars ?? {})) {
      s = s.replace(`{${key}}`, String(v));
    }
    return s;
  };
}

const STATUS_CLS: Record<FactorCandidate["status"], string> = {
  pending_review: "border-gold/40 text-gold",
  registered: "border-bull/35 text-bull",
  rejected: "border-fox-red/40 text-fox-red",
};

interface CandidatesResp {
  available: boolean;
  candidates: FactorCandidate[];
}

/** 因子候选审核区块(/factors 页) —— register 门的唯一人工入口(ADR-0019)。 */
export function FactorCandidates() {
  const t = useStr();
  const { data, mutate } = useSWR<CandidatesResp>(
    "/api/factors/candidates",
    jsonFetcher,
    { refreshInterval: REFRESH_MS, keepPreviousData: true },
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!data) return null;

  const review = async (id: string, action: "register" | "reject") => {
    setBusy(id);
    setError(null);
    try {
      const resp = await fetch(`/api/factors/candidates/${id}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      // fetch 不会因 4xx/5xx 抛错：register 门是唯一人工入口，静默吞掉
      // 502（服务宕机）/ 400（UUID 非法）代价最高，必须显式提示。
      const payload = (await resp.json().catch(() => null)) as {
        error?: string;
      } | null;
      if (!resp.ok) {
        throw new Error(payload?.error ?? resp.statusText);
      }
      await mutate();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Panel title={t("title")}>
      <div className="flex flex-col gap-3 px-4 py-3">
        <p className="text-xs text-fg-subtle">{t("hint")}</p>
        {error && (
          <p
            role="alert"
            className="rounded border border-fox-red/40 bg-fox-red/10 px-3 py-2 text-xs text-fox-red"
          >
            {t("reviewFailed", { msg: error })}
          </p>
        )}
        {!data.available ? (
          <p className="text-sm text-fg-muted">{t("unavailable")}</p>
        ) : data.candidates.length === 0 ? (
          <p className="text-sm text-fg-muted">{t("empty")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {data.candidates.map((c) => (
            <li
              key={c.id}
              className="rounded border border-line/60 bg-bg-elev/40 px-3 py-2"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <code className="block truncate font-mono text-xs text-fg">
                    {c.expression}
                  </code>
                  <p className="mt-1 text-xs text-fg-muted">
                    <span className="text-fg-subtle">{t("hypothesis")}:</span>{" "}
                    {c.hypothesis}
                  </p>
                  <p className="mt-1 flex flex-wrap gap-x-3 font-mono text-[11px] text-fg-subtle">
                    {testStat(c, "rank_ic")}
                    {testStat(c, "icir")}
                    {testStat(c, "max_corr")}
                    {typeof c.test_results.decay_state === "string" && (
                      <span>decay={String(c.test_results.decay_state)}</span>
                    )}
                    <span>{t("nTested", { n: c.n_tested })}</span>
                    {c.symbol && (
                      <span>
                        {t("ctx")}: {c.venue}/{c.symbol}/{c.timeframe}
                      </span>
                    )}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={c.status} t={t} />
                  {c.status === "pending_review" && (
                    <>
                      <button
                        type="button"
                        disabled={busy === c.id}
                        onClick={() => review(c.id, "register")}
                        className="rounded border border-bull/40 px-2 py-0.5 text-xs text-bull hover:bg-bull/10 disabled:opacity-50"
                      >
                        {t("register")}
                      </button>
                      <button
                        type="button"
                        disabled={busy === c.id}
                        onClick={() => review(c.id, "reject")}
                        className="rounded border border-fox-red/40 px-2 py-0.5 text-xs text-fox-red hover:bg-fox-red/10 disabled:opacity-50"
                      >
                        {t("reject")}
                      </button>
                    </>
                  )}
                </div>
              </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}

function testStat(c: FactorCandidate, key: string) {
  const v = c.test_results[key];
  if (typeof v !== "number") return null;
  return (
    <span key={key}>
      {key}={v.toFixed(4)}
    </span>
  );
}

function StatusBadge({
  status,
  t,
}: {
  status: FactorCandidate["status"];
  t: ReturnType<typeof useStr>;
}) {
  const label =
    status === "pending_review"
      ? t("pending")
      : status === "registered"
        ? t("registered")
        : t("rejected");
  const Icon =
    status === "pending_review"
      ? CircleDashed
      : status === "registered"
        ? CheckCircle2
        : XCircle;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        STATUS_CLS[status],
      )}
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}
