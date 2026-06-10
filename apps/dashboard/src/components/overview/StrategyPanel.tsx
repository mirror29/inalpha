"use client";

import { useLocale, useTranslations } from "next-intl";

import type { StrategyCandidateSummary } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";
import { Panel } from "@/components/ui/Panel";
import { CandidateStatusBadge } from "@/components/ui/StatusBadge";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** 从 metrics 取一个数值指标(缺失/非数返回 null)。 */
function metric(c: StrategyCandidateSummary, key: string): number | null {
  const v = c.metrics?.[key];
  return typeof v === "number" ? v : null;
}

/** 数值方向 → 颜色(正绿负红);null = 灰。 */
function signCls(v: number | null): string {
  if (v === null) return "text-fg-muted/50";
  if (v > 0) return "text-bull";
  if (v < 0) return "text-fox-red";
  return "text-fg";
}

/**
 * 总览的策略池面板 —— 把「系统当前有哪些策略」搬上总控制台:
 * 后端按 fitness DESC 排序,取头部若干条,显示状态 / 适应度 / 回测收益。
 * 行可点进策略详情;标题右侧给晋级计数 + 「查看全部」到策略实验室。
 *
 * 数据复用总览 payload 的 `candidates` / `candidateCounts`(BFF 已 fan-out),不额外请求。
 */
export function StrategyPanel({
  candidates,
  counts,
}: {
  candidates: StrategyCandidateSummary[];
  counts: { all: number; promoted: number; candidate: number };
}) {
  const t = useTranslations("overview.strategyPanel");
  const locale = useLocale();

  return (
    <Panel
      title={t("title")}
      aside={
        <div className="flex items-center gap-3">
          <span
            className={cn(
              "tnum font-mono text-xs",
              counts.promoted > 0 ? "text-bull" : "text-fg-muted",
            )}
          >
            {t("promoted", { promoted: counts.promoted, all: counts.all })}
          </span>
          <Link
            href="/lab"
            className="font-mono text-xs text-fg-muted transition-colors hover:text-cyan"
          >
            {t("viewAll")} →
          </Link>
        </div>
      }
    >
      {candidates.length === 0 ? (
        <TableEmpty>{t("empty")}</TableEmpty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <TableHeadRow>
                <Th>{t("col.strategy")}</Th>
                <Th>{t("col.status")}</Th>
                <Th right>{t("col.fitness")}</Th>
                <Th right>{t("col.return")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {candidates.map((c) => {
                const ret = metric(c, "total_return_pct");
                return (
                  <tr
                    key={c.id}
                    className="border-t border-border-subtle/60 transition-colors hover:bg-bg-elev/30"
                  >
                    <Td>
                      <Link
                        href={`/lab/${c.id}`}
                        className="text-fg transition-colors hover:text-cyan"
                      >
                        {c.description?.trim() || c.code_hash}
                      </Link>
                    </Td>
                    <Td>
                      <CandidateStatusBadge
                        status={c.status}
                        label={t(`status.${c.status}`)}
                      />
                    </Td>
                    <Td right mono>
                      <span className={signCls(c.fitness)}>
                        {c.fitness === null ? "—" : fmtNum(c.fitness, locale, 3)}
                      </span>
                    </Td>
                    <Td right mono>
                      {ret === null ? (
                        <span className="text-fg-muted/40">—</span>
                      ) : (
                        <span className={signCls(ret)}>
                          {ret > 0 ? "+" : ret < 0 ? "−" : ""}
                          {fmtNum(Math.abs(ret), locale, 2)}%
                        </span>
                      )}
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
