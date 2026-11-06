"use client";

import { useState } from "react";
import { useLocale } from "next-intl";
import { Panel } from "@/components/ui/Panel";
import { Button } from "@/components/ui/button";
import { Link } from "@/i18n/navigation";
import { EventQualityNotice, type EventQuality } from "./EventQualityNotice";

export type CoverageMatrix = {
  complete: boolean;
  sampled_at: string;
  recommended_target_id: string | null;
  rows: Array<{ asset: string; target_id: string | null; status: string;
    minimum_matched_event_pairs?: number; independent_event_upper_bound?: number; blocker_codes?: string[];
    preparation?: { event_quality?: EventQuality; config?: { timeframe: string; from_ts: string; as_of: string };
      selection_fact_count?: number;
      target?: { evidence?: { seed_label?: string } };
      selection_start?: string; selection_end?: string;
      partition_bar_counts?: { discovery: number; selection: number; holdout: number };
      event_availability?: { first_available_at: string | null; snapshot_fact_versions: number };
      dataset_manifest?: { latest_bar_ts: string; cutoff_bar_ts: string; freshness_lag_seconds: number; warnings: string[] }; independent_upper_bound_by_type?: Record<string, number> } }>;
};

/** Compare necessary inputs only; a recommendation is never execution authority. */
export function EvolutionCoverageMatrix() {
  const zh = useLocale() === "zh";
  const [matrix, setMatrix] = useState<CoverageMatrix | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const text = (cn: string, en: string) => zh ? cn : en;
  const check = async () => {
    setBusy(true); setFailed(false); setMatrix(null);
    try {
      const response = await fetch("/api/evolution/prepare", { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "coverage_matrix" }) });
      if (!response.ok) throw new Error("unavailable");
      setMatrix(await response.json());
    } catch { setFailed(true); } finally { setBusy(false); }
  };
  return <Panel title={text("首轮实验 · BTC / ETH / SOL", "First experiment · BTC / ETH / SOL")}>
    <div className="space-y-4 p-4 sm:p-5">
      <p className="text-sm text-fg-muted">{text("同时检查 BTC、ETH、SOL 的已有模拟盘。每个资产固定选择最早启动的运行中策略；只比较数据覆盖，不比较收益。", "Check existing BTC, ETH and SOL paper runners at one sampling time. Use the oldest running supported runner per asset; compare input coverage, never returns.")}</p>
      <Button disabled={busy} onClick={() => void check()}>{busy ? text("正在检查…", "Checking…") : text("免费检查三个资产", "Check three assets for free")}</Button>
      <div aria-live="polite">
        {failed && <p role="alert" className="text-fox-red">{text("覆盖检查未完成，请检查登录或服务连接后重试。", "Coverage check unavailable. Check your session or service connection.")}</p>}
        {matrix && <CoverageMatrixResults matrix={matrix} zh={zh} />}
      </div>
    </div>
  </Panel>;
}

/** Render a read-only input comparison; each link requires a fresh preparation. */
export function CoverageMatrixResults({ matrix, zh }: { matrix: CoverageMatrix; zh: boolean }) {
  const text = (cn: string, en: string) => zh ? cn : en;
  return <div className="space-y-4">
          {!matrix.complete && <p role="alert" className="text-gold">{text("部分资产检查失败，本轮不推荐首选实验。请重试完整盘点。", "Some asset checks failed. No experiment is recommended until the complete inventory succeeds.")}</p>}
          <p className="font-mono text-xs text-fg-muted">UTC · {matrix.sampled_at}</p>
          <div className="grid gap-3 lg:grid-cols-3">{matrix.rows.map(row => <div key={row.asset} className="space-y-3 rounded-lg border border-border-subtle p-4">
            <h3 className="font-mono text-cyan">{row.asset} · {row.preparation?.config?.timeframe ?? "—"}</h3>
            {row.preparation?.target?.evidence?.seed_label && <p className="text-sm text-fg-muted">{row.preparation.target.evidence.seed_label}</p>}
            <p>{row.status === "necessary_inputs_present" ? text("必要输入具备，尚未正式评估", "Necessary inputs present; evaluation unverified") : row.status === "existing_experiment" ? text("已有实验，保持冻结", "Existing frozen experiment") : text("暂不可启动", "Not ready to start")}</p>
            <p className="font-mono text-sm">{text("事实 / 独立上界", "Facts / independent upper bound")}: {row.preparation?.selection_fact_count ?? "—"} / {row.independent_event_upper_bound ?? "—"} · {text("所需匹配对照门槛", "Required matched-pair threshold")} {row.minimum_matched_event_pairs ?? "—"}</p>
            {row.preparation?.config && <p className="break-words font-mono text-xs text-fg-muted">{row.preparation.config.from_ts} → {row.preparation.config.as_of}</p>}
            <EventQualityNotice quality={row.preparation?.event_quality} zh={zh} />
            {row.preparation && <details className="text-xs text-fg-muted">
              <summary className="cursor-pointer">{text("窗口与输入诊断", "Window and input diagnostics")}</summary>
              <div className="mt-3 space-y-2 break-words">
                {row.preparation.partition_bar_counts && <p>{text("发现 / 选择 / 封存行情根数", "Discovery / selection / holdout bars")}: {row.preparation.partition_bar_counts.discovery} / {row.preparation.partition_bar_counts.selection} / {row.preparation.partition_bar_counts.holdout}</p>}
                {row.preparation.selection_start && <p>{text("选择段（UTC）", "Selection (UTC)")}: {row.preparation.selection_start} → {row.preparation.selection_end}</p>}
                {row.preparation.event_availability && <p>{text("快照最早可见时间 / 事实版本数", "Earliest snapshot availability / fact versions")}: {row.preparation.event_availability.first_available_at ?? "—"} / {row.preparation.event_availability.snapshot_fact_versions}</p>}
                {row.preparation.dataset_manifest && <>
                  <p>{text("最新行情 / 要求末根（UTC）", "Latest bar / required cutoff (UTC)")}: {row.preparation.dataset_manifest.latest_bar_ts} / {row.preparation.dataset_manifest.cutoff_bar_ts}</p>
                  <p>{text("新鲜度差值（秒）", "Freshness lag (seconds)")}: {row.preparation.dataset_manifest.freshness_lag_seconds}</p>
                  {row.preparation.dataset_manifest.warnings.map(warning => <p key={warning} className="text-gold">{warning}</p>)}
                </>}
                {row.preparation.independent_upper_bound_by_type && <dl className="grid grid-cols-2 gap-2">{Object.entries(row.preparation.independent_upper_bound_by_type).map(([type, count]) => <div key={type}><dt>{type}</dt><dd className="font-mono">{count}</dd></div>)}</dl>}
              </div>
            </details>}
            {row.blocker_codes?.length ? <p className="break-words text-xs text-gold">{row.blocker_codes.includes("ASSET_IDENTITY_UNAVAILABLE") ? text("部分标的身份解析失败，无法确认完整资产范围；请检查资产映射后重试", "Some asset identities could not be resolved; check market mappings before retrying") : row.blocker_codes.includes("RUNNER_INVENTORY_INCOMPLETE") ? text("运行任务列表达到盘点上限，请从策略详情逐项准备", "Runner inventory limit reached; prepare individually from strategy details") : row.blocker_codes.includes("OWNED_RUNNER_UNAVAILABLE") ? text("没有运行中的受支持模拟盘", "No running supported paper runner") : row.blocker_codes.includes("E2_INPUT_COVERAGE_INSUFFICIENT") ? text("选择段独立事件上界不足", "Selection independent-event upper bound is insufficient") : text("准备受阻，打开详情检查数据或服务状态", "Preparation blocked; inspect data or service status")}</p> : null}
            {matrix.recommended_target_id === row.target_id && row.target_id && <p className="text-cyan">{text("本轮优先准备", "First preparation choice")}</p>}
            {row.target_id && <Link className="text-cyan hover:underline" href={`/evolution/prepare?targetKind=paper_runner&targetId=${row.target_id}`}>{text("查看并重新预检", "Inspect and recheck")}</Link>}
          </div>)}</div>
          <p className="text-xs text-fg-muted">{text("优先必要输入合格项，再按独立上界排序，同分按 BTC、ETH、SOL。达到上界不代表匹配对照、FDR 或冠军通过；进入详情后重新预检再审批。", "Eligible inputs first, then descending upper bound; ties use BTC, ETH, SOL. The bound does not certify matching, FDR or a champion. Recheck in detail before approval.")}</p>
        </div>;
}
