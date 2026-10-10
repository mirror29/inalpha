"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, RotateCcw } from "lucide-react";
import { useTranslations } from "next-intl";
import useSWR from "swr";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ErrorState, SkeletonBlock } from "@/components/ui/Feedback";
import { PageHeader } from "@/components/ui/PageHeader";
import { Panel } from "@/components/ui/Panel";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { TableEmpty, TableHeadRow, Td, Th } from "@/components/ui/Table";
import { formatUsageCost } from "@/lib/usage-format";

const SERVICES = ["orchestration", "research", "evolver"] as const;
const STATUSES = [
  "pending",
  "completed",
  "failed",
  "cancelled",
  "interrupted",
  "invalid",
  "truncated",
] as const;
type UsageStatus = (typeof STATUSES)[number];

type UsageRow = {
  call_id: string;
  service: string;
  stage: string;
  model: string | null;
  input_tokens: string | null;
  output_tokens: string | null;
  estimated_cost_usd: string | null;
  status: UsageStatus;
  created_at: string;
};
type UsagePayload = {
  summary: {
    calls: number;
    input_tokens: string | null;
    output_tokens: string | null;
    known_cost_usd: string | null;
    unknown_usage_calls: number;
    unknown_cost_calls: number;
    pending_calls: number;
  };
  items: UsageRow[];
  nextOffset: number | null;
};

/** Align ledger states with the console's existing status palette. */
function statusTone(status: UsageStatus) {
  if (status === "completed") return "bull";
  if (status === "failed" || status === "invalid") return "fox";
  if (status === "pending" || status === "truncated") return "gold";
  return "muted";
}

/** Show partial totals explicitly; absent provider receipts never render as zero cost. */
export function UsageClient({
  initialOperation = "",
}: {
  initialOperation?: string;
}) {
  const t = useTranslations("usage");
  const [service, setService] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [operation, setOperation] = useState(initialOperation);
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({
    service,
    model,
    status,
    from,
    to,
    operation,
    offset: String(offset),
  });
  const { data, error, isLoading, mutate } = useSWR<UsagePayload>(
    `/api/usage?${params}`,
    async (url: string) => {
      const response = await fetch(url);
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    },
  );
  const money = (value: string | null) =>
    value === null ? t("unknown") : formatUsageCost(value);
  const resetFilters = () => {
    setService("");
    setModel("");
    setStatus("");
    setFrom("");
    setTo("");
    setOperation("");
    setOffset(0);
  };

  return (
    <div className="min-w-0 space-y-6">
      <PageHeader title={t("title")} subtitle={t("description")} />
      <Panel
        title={t("filters")}
        aside={
          <Button
            variant="ghost"
            size="sm"
            onClick={resetFilters}
            disabled={
              !service && !model && !status && !from && !to && !operation
            }
          >
            <RotateCcw aria-hidden="true" />
            {t("reset")}
          </Button>
        }
      >
        <div className="grid gap-4 p-4 sm:grid-cols-2 xl:grid-cols-3">
          <div className="min-w-0 space-y-2">
            <Label htmlFor="usage-service">{t("service")}</Label>
            <Select
              value={service || "all"}
              onValueChange={(value) => {
                setService(value === "all" ? "" : value);
                setOffset(0);
              }}
            >
              <SelectTrigger id="usage-service">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("all")}</SelectItem>
                {SERVICES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`services.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="min-w-0 space-y-2">
            <Label htmlFor="usage-status">{t("status")}</Label>
            <Select
              value={status || "all"}
              onValueChange={(value) => {
                setStatus(value === "all" ? "" : value);
                setOffset(0);
              }}
            >
              <SelectTrigger id="usage-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("all")}</SelectItem>
                {STATUSES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`statuses.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {(
            [
              ["model", model, setModel],
              ["operation", operation, setOperation],
              ["from", from, setFrom],
              ["to", to, setTo],
            ] as const
          ).map(([key, value, setter]) => (
            <div key={key} className="min-w-0 space-y-2">
              <Label htmlFor={`usage-${key}`}>{t(key)}</Label>
              <Input
                id={`usage-${key}`}
                type={key === "from" || key === "to" ? "date" : "text"}
                value={value}
                className="min-w-0 font-mono text-xs"
                onChange={(event) => {
                  setter(event.target.value);
                  setOffset(0);
                }}
              />
            </div>
          ))}
        </div>
      </Panel>
      {error && (
        <div role="alert">
          <ErrorState message={t("error")} onRetry={() => void mutate()} />
        </div>
      )}
      {isLoading && (
        <div role="status" aria-label={t("loading")} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            {[0, 1, 2].map((key) => (
              <SkeletonBlock key={key} className="h-28" />
            ))}
          </div>
          <SkeletonBlock className="h-64" />
        </div>
      )}
      {data && (
        <>
          <dl className="grid gap-4 sm:grid-cols-3">
            {(
              [
                ["calls", data.summary.calls],
                [
                  "tokens",
                  `${data.summary.input_tokens ?? "—"} / ${data.summary.output_tokens ?? "—"}`,
                ],
                ["cost", money(data.summary.known_cost_usd)],
              ] as const
            ).map(([key, value]) => (
              <div
                key={key}
                className="min-w-0 rounded-xl border border-border-subtle border-t-cyan/40 bg-bg-elev/30 p-4"
              >
                <dt className="text-xs text-fg-muted">{t(key)}</dt>
                <dd className="tnum mt-3 break-all font-mono text-xl tracking-tight text-fg lg:text-2xl">
                  {value}
                </dd>
              </div>
            ))}
          </dl>
          <Panel
            title={t("records")}
            aside={
              <span className="tnum font-mono text-xs text-fg-muted">
                {data.summary.calls}
              </span>
            }
          >
            {(data.summary.unknown_usage_calls > 0 ||
              data.summary.unknown_cost_calls > 0 ||
              data.summary.pending_calls > 0) && (
              <p className="border-b border-border-subtle px-4 py-3 text-xs leading-relaxed text-fg-muted">
                {t("incomplete", {
                  usage: data.summary.unknown_usage_calls,
                  cost: data.summary.unknown_cost_calls,
                  pending: data.summary.pending_calls,
                })}
              </p>
            )}
            {data.items.length ? (
              <div className="overflow-x-auto">
                <table className="w-full whitespace-nowrap text-sm">
                  <thead>
                    <TableHeadRow>
                      {[
                        "time",
                        "service",
                        "model",
                        "status",
                        "tokens",
                        "cost",
                      ].map((key) => (
                        <Th
                          key={key}
                          right={key === "tokens" || key === "cost"}
                        >
                          {t(key)}
                        </Th>
                      ))}
                    </TableHeadRow>
                  </thead>
                  <tbody className="divide-y divide-border-subtle/60">
                    {data.items.map((row) => (
                      <tr
                        key={row.call_id}
                        className="transition-colors hover:bg-bg-elev/60"
                      >
                        <Td mono muted>
                          <time dateTime={row.created_at} className="text-xs">
                            {new Date(row.created_at)
                              .toISOString()
                              .slice(0, 10)}
                            <span className="mt-1 block text-fg-muted/70">
                              {new Date(row.created_at)
                                .toISOString()
                                .slice(11, 19)}
                            </span>
                          </time>
                        </Td>
                        <Td>
                          {SERVICES.includes(
                            row.service as (typeof SERVICES)[number],
                          )
                            ? t(`services.${row.service}`)
                            : row.service}
                          <div className="mt-1 font-mono text-[10px] text-fg-muted">
                            {row.stage}
                          </div>
                        </Td>
                        <Td mono className="text-xs">
                          {row.model ?? "—"}
                        </Td>
                        <Td>
                          <StatusBadge
                            label={t(`statuses.${row.status}`)}
                            tone={statusTone(row.status)}
                            dot
                          />
                        </Td>
                        <Td mono right className="text-xs">
                          {row.input_tokens ?? "—"} / {row.output_tokens ?? "—"}
                        </Td>
                        <Td mono right className="text-xs">
                          {money(row.estimated_cost_usd)}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <TableEmpty>{t("empty")}</TableEmpty>
            )}
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle px-4 py-3">
              <span className="tnum font-mono text-xs text-fg-muted">
                {t("page", {
                  current: Math.floor(offset / 50) + 1,
                  total: Math.max(1, Math.ceil(data.summary.calls / 50)),
                })}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - 50))}
                >
                  <ChevronLeft aria-hidden="true" />
                  {t("previous")}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={data.nextOffset === null}
                  onClick={() => setOffset(data.nextOffset ?? offset)}
                >
                  {t("next")}
                  <ChevronRight aria-hidden="true" />
                </Button>
              </div>
            </div>
          </Panel>
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="ghost" size="sm">
                {t("accountingHelp")}
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{t("accountingHelp")}</DialogTitle>
                <DialogDescription>{t("limits")}</DialogDescription>
              </DialogHeader>
            </DialogContent>
          </Dialog>
        </>
      )}
    </div>
  );
}
