"use client";

import { useLocale, useTranslations } from "next-intl";

import type { StrategyRunRecord } from "@/lib/types";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import { fmtRelative, fmtSigned, instrumentLabel, pnlColor } from "@/lib/format";
import { Panel } from "@/components/ui/Panel";
import { Td, TableEmpty, TableHeadRow, Th } from "@/components/ui/Table";

/** 总控制台展示的 run 行数上限 —— 超出走「查看全部」到 Live Runner 页。 */
const SHOWN = 6;

/** run 状态 → 状态灯颜色(运行绿 / 停止灰 / 错误红)。 */
const STATUS_DOT: Record<StrategyRunRecord["status"], string> = {
  running: "bg-bull",
  stopped: "bg-fg-muted/50",
  errored: "bg-fox-red",
};

/**
 * 总览的 Live Runner 面板 —— 把「当前在自动跑的策略」直接搬上总控制台:
 * 运行中优先、再按启动时间倒序,显示标的 / 周期 / 累计盈亏 / 最后处理的 bar。
 * 行可点进 run 详情;超过 {@link SHOWN} 条走标题右侧「查看全部」。
 *
 * 数据复用总览 payload 的 `runs`(BFF 已 fan-out),不额外请求。
 */
export function RunnersPanel({ runs }: { runs: StrategyRunRecord[] }) {
  const t = useTranslations("overview.runnersPanel");
  const locale = useLocale();
  const nowMs = Date.now();

  const runningCount = runs.filter((r) => r.status === "running").length;
  // 按策略(candidate)去重:重启会新建 run 记录,这里只展示每个策略最新一次
  // run —— 与 Live Runner 页的分组口径一致,完整历史去该页的卡片里看。
  const latestByCandidate = new Map<string, StrategyRunRecord>();
  for (const r of runs) {
    const prev = latestByCandidate.get(r.candidate_id);
    if (!prev || r.started_at.localeCompare(prev.started_at) > 0) {
      latestByCandidate.set(r.candidate_id, r);
    }
  }
  // 运行中排前,同状态按启动时间倒序(最近启动的在上)。
  const sorted = [...latestByCandidate.values()].sort((a, b) => {
    const ar = a.status === "running" ? 0 : 1;
    const br = b.status === "running" ? 0 : 1;
    if (ar !== br) return ar - br;
    return b.started_at.localeCompare(a.started_at);
  });
  const shown = sorted.slice(0, SHOWN);

  return (
    // h-full:总览里与持仓并排,grid stretch 下两卡等高(父容器非 stretch 时为 no-op)。
    <Panel
      className="h-full"
      title={t("title")}
      aside={
        <div className="flex items-center gap-3">
          <span className="tnum font-mono text-xs text-bull">
            {t("running", { count: runningCount })}
          </span>
          <Link
            href="/runners"
            className="font-mono text-xs text-fg-muted transition-colors hover:text-cyan"
          >
            {t("viewAll")} →
          </Link>
        </div>
      }
    >
      {runs.length === 0 ? (
        <TableEmpty>{t("empty")}</TableEmpty>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <TableHeadRow>
                <Th>{t("col.status")}</Th>
                <Th>{t("col.instrument")}</Th>
                <Th>{t("col.tf")}</Th>
                <Th right>{t("col.cumulativePnl")}</Th>
                <Th right>{t("col.lastBar")}</Th>
              </TableHeadRow>
            </thead>
            <tbody>
              {shown.map((r) => (
                <tr
                  key={r.id}
                  className="border-t border-border-subtle/60 transition-colors hover:bg-bg-elev/30"
                >
                  <Td>
                    <Link
                      href={`/runners/${r.id}`}
                      className="inline-flex items-center gap-2 text-fg-muted transition-colors hover:text-fg"
                    >
                      <span
                        className={cn(
                          "inline-block size-2 shrink-0 rounded-full",
                          STATUS_DOT[r.status],
                          r.status === "running" && "caret-blink",
                        )}
                      />
                      {t(`status.${r.status}`)}
                    </Link>
                  </Td>
                  <Td>
                    <Link
                      href={`/runners/${r.id}`}
                      className="text-fg transition-colors hover:text-cyan"
                    >
                      {instrumentLabel(r.symbol, r.venue)}
                    </Link>
                    {/* 现货/合约一眼可辨:perp 带杠杆倍数,spot 灰字轻量不抢视线 */}
                    <span
                      className={cn(
                        "ml-1.5 rounded px-1 py-0.5 font-mono text-[10px]",
                        r.trading_mode === "perp"
                          ? "bg-gold/10 text-gold"
                          : "bg-bg-elev text-fg-muted",
                      )}
                    >
                      {r.trading_mode === "perp"
                        ? t("modePerp", { leverage: r.leverage })
                        : t("modeSpot")}
                    </span>
                  </Td>
                  <Td mono muted>
                    {r.timeframe}
                  </Td>
                  <Td right mono>
                    <span className={pnlColor(r.cumulative_pnl)}>
                      {fmtSigned(r.cumulative_pnl, null, locale)}
                    </span>
                  </Td>
                  <Td right mono muted>
                    {fmtRelative(r.last_bar_ts, nowMs, locale)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
