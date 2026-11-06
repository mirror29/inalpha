export type EventQuality = {
  status: string;
  blocker_codes?: string[];
  scope?: string;
  policy_version?: string;
  inspected_fact_count?: number;
  legacy_regulatory_fact_count?: number;
  unverified_provenance_fact_count?: number;
  extractor_version_counts?: Record<string, number>;
};

/** Explain known evidence risks without treating version checks as semantic validation. */
export function EventQualityNotice({ quality, zh }: { quality?: EventQuality; zh: boolean }) {
  if (!quality || quality.status === "not_checked" && !quality.blocker_codes?.length) return null;
  const text = (cn: string, en: string) => zh ? cn : en;
  const blocked = !!quality.blocker_codes?.length || quality.status === "review_required";
  return <div className="space-y-2 border-t border-border-subtle pt-3 text-sm text-fg-muted">
    {blocked && <div role="alert" className="space-y-2 text-gold">
      <p>{text("事件质量待核查，暂不能启动搜索。", "Event quality needs review; search cannot start.")}</p>
      {!!quality.legacy_regulatory_fact_count && <p>{text("发现上下文与选择段中可能受旧版分类错误影响的监管事实", "Legacy regulatory facts potentially affected by classification errors in discovery context/selection")}: {quality.legacy_regulatory_fact_count}</p>}
      {!!quality.unverified_provenance_fact_count && <p>{text("抽取来源版本尚未确认的事实", "Facts with unverified extractor provenance")}: {quality.unverified_provenance_fact_count}</p>}
      <p>{text("需先核查完整发现上下文的来源与分类，再准备新实验；新增事件不会自动修复旧证据。历史事实和原始数量统计保留。", "Review provenance and classification across the full discovery context before preparing a new experiment; new events do not automatically repair legacy evidence. Historical facts and raw counts are retained.")}</p>
    </div>}
    <details className="text-xs">
      <summary className="cursor-pointer">{text("抽取版本与质量检查", "Extractor versions and quality checks")}</summary>
      <div className="mt-2 space-y-2 break-words">
        <p>{text("检查范围：发现上下文与选择段（含窗口前已可见事实）", "Scope: discovery context and selection (including earlier visible facts)")} · {quality.inspected_fact_count ?? "—"}</p>
        {Object.entries(quality.extractor_version_counts ?? {}).map(([version, count]) => <p key={version} className="font-mono">{version}: {count}</p>)}
        <p>{text("此项仅检查旧版监管 ban 分类风险与来源版本；listing、SEC 等其他分类尚未核查，事件语义、匹配对照和 FDR 尚未验证。", "This checks legacy regulatory ban risks and provenance only; listing, SEC and other classifications are not audited; semantics, matched controls and FDR remain unverified.")}</p>
      </div>
    </details>
  </div>;
}
