"use client";

import { useLocale } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/button";
import { Link } from "@/i18n/navigation";

type Preparation = {
  status: string;
  event_snapshot_id?: string;
  seed_source_hash?: string;
  dataset_content_sha256?: string;
  blocker_codes?: string[];
  config?: { symbol: string; timeframe: string; from_ts: string; as_of: string };
  selection_start?: string;
  selection_end?: string;
  selection_fact_count?: number;
  independent_event_upper_bound?: number;
  minimum_matched_event_pairs?: number;
  partition_bar_counts?: { discovery: number; selection: number; holdout: number };
  inherited_window?: { from_ts: string; as_of: string };
  target?: { evidence?: { loop_id?: string; seed_label?: string } };
};

/** Keep editable draft inputs separate from the last checked immutable experiment. */
export function EvolutionPreparation({ targetKind, targetId }: { targetKind: string; targetId: string }) {
  const zh = useLocale() === "zh";
  const [result, setResult] = useState<Preparation | null>(null);
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [approval, setApproval] = useState<{ requestId: string; status?: string; preparation?: { estimated_max_cost_usd?: number }; toolInput?: { llm_snapshot?: { provider: string; model: string } } } | null>(null);
  const [loopId, setLoopId] = useState<string | null>(null);
  const sequence = useRef(0);
  useEffect(() => {
    const requestId = new URL(window.location.href).searchParams.get("approval");
    if (!requestId) return;
    setRecovering(true);
    let active = true;
    setBusy(true);
    void fetch(`/api/evolution/approval?requestId=${encodeURIComponent(requestId)}`).then(async response => {
      if (!response.ok) throw new Error("unavailable");
      const data = await response.json();
      if (!active) return;
      if (!["approved", "pending"].includes(data.status)) throw new Error("expired");
      setApproval({ ...data, preparation: data.toolInput?.preparation });
      const config = data.toolInput?.request?.experiment?.config;
      if (config) { setFrom(config.from_ts.slice(0, 16)); setUntil(config.as_of.slice(0, 16)); }
    }).catch(() => { if (active) setError(true); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);

  const text = (cn: string, en: string) => zh ? cn : en;
  const changed = () => { sequence.current += 1; setResult(null); setApproval(null); setError(false); setBusy(false); };
  const check = async (suggest: boolean) => {
    const version = ++sequence.current;
    setBusy(true); setError(false); setResult(null); setApproval(null);
    try {
      const response = await fetch("/api/evolution/prepare", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ targetKind, targetId, ...(!suggest ? { window: {
          from_ts: new Date(`${from}:00Z`).toISOString(), as_of: new Date(`${until}:00Z`).toISOString(),
        } } : {}) }),
      });
      if (!response.ok) throw new Error("preparation unavailable");
      const data = await response.json() as Preparation;
      if (version !== sequence.current) return;
      setResult(data);
      if (data.config) { setFrom(data.config.from_ts.slice(0, 16)); setUntil(data.config.as_of.slice(0, 16)); }
    } catch { if (version === sequence.current) setError(true); }
    finally { if (version === sequence.current) setBusy(false); }
  };
  const requestApproval = async () => {
    if (!result?.config) return;
    const version = ++sequence.current;
    setBusy(true); setError(false);
    try {
      const response = await fetch("/api/evolution/approval", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        targetKind, targetId, budget: 4, experiment: {
          config: result.config,
          from_ts: result.config.from_ts, as_of: result.config.as_of,
          eventSnapshotId: result.event_snapshot_id, seed_source_hash: result.seed_source_hash,
          dataset_content_sha256: result.dataset_content_sha256,
        },
      }) });
      const data = await response.json();
      if (!response.ok || !data.requiresApproval || !data.requestId) throw new Error("approval unavailable");
      if (version === sequence.current) {
        setApproval(data);
        const url = new URL(window.location.href); url.searchParams.set("approval", data.requestId);
        window.history.replaceState(null, "", url);
      }
    } catch { if (version === sequence.current) setError(true); }
    finally { if (version === sequence.current) setBusy(false); }
  };
  const discardApproval = async () => {
    if (!approval || approval.status === "approved") return;
    setBusy(true);
    try {
      const response = await fetch(`/api/permissions/${encodeURIComponent(approval.requestId)}/respond`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision: "deny" }) });
      if (!response.ok && response.status !== 404) throw new Error("unavailable");
      setApproval(null); setResult(null);
      const url = new URL(window.location.href); url.searchParams.delete("approval"); window.history.replaceState(null, "", url);
    } catch { setError(true); } finally { setBusy(false); }
  };
  const approve = async () => {
    if (!approval) return;
    setBusy(true); setError(false);
    try {
      const response = await fetch(`/api/permissions/${encodeURIComponent(approval.requestId)}/respond`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision: "allow" }),
      });
      const data = await response.json();
      if (!response.ok || !/^[0-9a-f-]{36}$/i.test(data.execution?.loop_id ?? "")) throw new Error("execution unavailable");
      setLoopId(data.execution.loop_id); setApproval(null);
    } catch { setError(true); }
    finally { setBusy(false); }
  };
  /** Format research timestamps in UTC without exposing transport serialization. */
  const date = (value?: string) => {
    if (!value) return "—";
    const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`;
    const parsed = new Date(normalized);
    return Number.isNaN(parsed.getTime()) ? value : `${parsed.toISOString().replace("T", " ").replace(/Z$/, "")} UTC`;
  };
  const input = "mt-2 block h-10 w-full min-w-0 rounded-md border border-border-subtle bg-bg-elev/50 px-3 font-mono text-sm text-fg outline-none transition-colors focus:border-cyan focus:ring-1 focus:ring-cyan disabled:opacity-50";
  return <section className="flex flex-col gap-6 text-sm text-fg">
    <Link href="/evolution" className="text-sm text-fg-muted">← {text("策略演化", "Evolution")}</Link>
    <PageHeader title={text("准备演化实验", "Prepare an experiment")} subtitle={text("选择研究窗口，检查数据覆盖，再确认实验费用。", "Choose a research window, check coverage, then review the cost.")} right={<span className="font-mono text-xs text-fg-muted">{text("准备 → 审批 → 搜索", "PREPARE → APPROVE → SEARCH")}</span>} />
    <Panel title={text("研究窗口", "Research window")} aside={<span className="font-mono text-xs text-cyan">{text("免费预检 · UTC", "FREE PREFLIGHT · UTC")}</span>}>
    <div className="space-y-5 p-4 sm:p-5">
    <Button disabled={busy || !!approval || !!loopId} onClick={() => void check(true)}>{text("使用建议窗口", "Use suggested window")}</Button>
    <p className="text-sm text-fg-muted">{text("默认窗口参考事件首次可见时间与最新闭合行情，不根据收益选择窗口，也不保证覆盖足够。时间统一为 UTC。", "The default uses event availability and closed bars, never returns. Sufficient coverage is not guaranteed. All times are UTC.")}</p>
    <div className="grid gap-4 sm:grid-cols-2">
      <label className="block text-xs text-fg-muted">{text("开始时间（UTC）", "Start (UTC)")}<input type="datetime-local" disabled={busy || !!approval || !!loopId} value={from} onChange={e => { changed(); setFrom(e.target.value); }} className={input} /></label>
      <label className="block text-xs text-fg-muted">{text("截止时间（UTC）", "Cutoff (UTC)")}<input type="datetime-local" disabled={busy || !!approval || !!loopId} value={until} onChange={e => { changed(); setUntil(e.target.value); }} className={input} /></label>
    </div>
    <Button disabled={busy || !!approval || !!loopId || !from || !until || from >= until} onClick={() => void check(false)}>{text("检查此窗口", "Check this window")}</Button>
    </div>
    </Panel>
    <div aria-live="polite" className="space-y-4">
      {busy && <p className="animate-pulse text-fg-muted">{text("正在核对种子、闭合行情和事件覆盖…", "Checking the seed, closed bars and event coverage…")}</p>}
      {error && <p role="alert" className="rounded-lg border border-fox-red/25 bg-fox-red/5 p-4 text-fox-red">{approval || recovering ? text("原审批任务的状态尚未确认。请恢复原任务或查看演化列表，避免重复创建。", "The original approval status is unconfirmed. Recover that task or inspect the evolution list before creating another.") : text("准备未完成。请检查时间范围、登录状态、模型配置和服务连接后重试；未启动生成。", "Preparation did not finish. Check the window, session, model configuration and service connection. Generation has not started.")}</p>}
      {loopId && <Link href={`/evolution/loops/${loopId}`}>{text("查看已提交的演化实验", "View submitted experiment")}</Link>}
      {approval && <div className="space-y-4 rounded-xl border border-border-subtle bg-bg-elev/30 p-5">
        <h2 className="font-display text-xl">{text("冻结实验与费用审批", "Frozen experiment approval")}</h2>
        <p>{approval.toolInput?.llm_snapshot?.provider} / {approval.toolInput?.llm_snapshot?.model}</p>
        <p>{text("生成费用上限（不含聊天）", "Generation budget ceiling (excluding chat)")}: ${approval.preparation?.estimated_max_cost_usd ?? "—"}</p>
        <p>{text("4 个基线候选，随后五代事件搜索；失败或证据不足会停止，不自动采用或下单。", "4 baseline candidates, followed by five generations of event search. Failures or insufficient evidence stop the experiment; no automatic adoption or orders.")}</p>
        <Button variant="outline" disabled={busy || approval.status === "approved"} onClick={() => void discardApproval()}>{text("返回修改，重新预检", "Edit and prepare again")}</Button>
        <Button disabled={busy || (approval.status !== "approved" && typeof approval.preparation?.estimated_max_cost_usd !== "number")} onClick={() => void approve()}>{approval.status === "approved" ? text("恢复已批准任务", "Recover approved execution") : text("批准并提交此实验", "Approve and submit this experiment")}</Button>
      </div>}
      {result && <div className="space-y-4 rounded-xl border border-border-subtle bg-bg-elev/30 p-4 sm:p-5">
        <h2 className={`font-display text-xl ${result.status === "blocked" ? "text-gold" : "text-fg"}`}>{result.status === "existing_experiment" ? text("已有实验", "Existing experiment") : result.status === "blocked" ? text("数据尚不足，未启动搜索", "Insufficient inputs; search not started") : text("必要输入具备，尚未通过评估", "Necessary inputs present; evaluation pending")}</h2>
        {result.target?.evidence?.loop_id && <Link href={`/evolution/loops/${result.target.evidence.loop_id}`}>{text("继续查看已有实验", "Continue existing experiment")}</Link>}
        {result.blocker_codes?.includes("EVENT_SNAPSHOT_EMPTY") && <p>{text("当前标的在截止时点没有可见事件；需继续真实采集后准备新实验。", "No visible events for this asset at the cutoff. Collect real evidence before preparing a new experiment.")}</p>}
        {result.blocker_codes?.some(code => code.startsWith("EVOLUTION_DATA_")) && <p>{text("行情窗口尚不可用：请核对连续闭合行情、数据新鲜度及最多 10,000 根限制。", "The bar window is unavailable: check continuity, freshness and the 10,000-bar limit.")}</p>}
        {result.target?.evidence?.seed_label && <p className="max-w-3xl leading-relaxed text-fg-muted">{result.target.evidence.seed_label}</p>}
        {result.config && <p className="font-mono text-sm text-cyan">{result.config.symbol} · {result.config.timeframe}</p>}
        {result.selection_start && <p className="break-words font-mono text-xs text-fg-muted">{text("选择段", "Selection window")}: {date(result.selection_start)} — {date(result.selection_end)}</p>}
        {result.partition_bar_counts && <p className="border-t border-border-subtle pt-4 font-mono text-sm">{text("发现 / 选择 / 封存行情根数", "Discovery / selection / sealed bars")}: {result.partition_bar_counts.discovery} / {result.partition_bar_counts.selection} / {result.partition_bar_counts.holdout}</p>}
        {result.selection_fact_count !== undefined && <p className="font-mono text-sm">{text("选择段事实数", "Selection facts")}: {result.selection_fact_count} · {text("独立事件上界", "Independent-event upper bound")}: {result.independent_event_upper_bound} / {result.minimum_matched_event_pairs}</p>}
        <p className="max-w-3xl text-xs leading-relaxed text-fg-muted">{text("事件上界不是匹配对照数。匹配对照、FDR 与 holdout 尚未评估；等待新事件不能补齐已经冻结的旧窗口。", "The event upper bound is not a matched-control count. Matching, FDR and holdout remain unevaluated. New events cannot fill an old frozen window.")}</p>
        {result.status === "necessary_inputs_present" && !approval && !loopId && <Button disabled={busy} onClick={() => void requestApproval()}>{text("冻结输入并查看费用审批", "Freeze inputs and review cost approval")}</Button>}
        {result.inherited_window && <details className="border-t border-border-subtle pt-3 text-xs text-fg-muted"><summary className="cursor-pointer transition-colors hover:text-fg">{text("原策略窗口（未修改）", "Original strategy window (unchanged)")}</summary><p className="mt-3 break-words font-mono">{date(result.inherited_window.from_ts)} — {date(result.inherited_window.as_of)}</p></details>}
      </div>}
    </div>
  </section>;
}
