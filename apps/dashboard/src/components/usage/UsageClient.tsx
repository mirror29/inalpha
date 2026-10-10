"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import { formatUsageCost } from "@/lib/usage-format";

type UsageRow = {
  call_id: string; service: string; stage: string; model: string | null;
  input_tokens: string | null; output_tokens: string | null; estimated_cost_usd: string | null;
  status: string; created_at: string;
};
type UsagePayload = {
  summary: { calls: number; input_tokens: string | null; output_tokens: string | null;
    known_cost_usd: string | null; unknown_usage_calls: number; unknown_cost_calls: number; pending_calls: number };
  items: UsageRow[]; nextOffset: number | null;
};

/** Shows partial totals explicitly; absent provider receipts never render as zero cost. */
export function UsageClient({ initialOperation = "" }: { initialOperation?: string }) {
  const t = useTranslations("usage");
  const [service, setService] = useState("");
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [operation, setOperation] = useState(initialOperation);
  const [offset, setOffset] = useState(0);
  const params = new URLSearchParams({ service, model, status, from, to, operation, offset: String(offset) });
  const { data, error, isLoading } = useSWR<UsagePayload>(`/api/usage?${params}`, async (url: string) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  });
  const money = (value: string | null) => value === null ? t("unknown") : formatUsageCost(value);
  return <section className="space-y-6 p-6 text-fg">
    <header><h1 className="text-2xl font-semibold">{t("title")}</h1><p className="mt-2 text-sm text-fg-muted">{t("description")}</p></header>
    <div className="flex flex-wrap gap-3">
      <label>{t("service")} <select className="rounded border bg-surface p-2" value={service} onChange={e => { setService(e.target.value); setOffset(0); }}>
        <option value="">{t("all")}</option>{["orchestration", "research", "evolver"].map(value => <option key={value}>{value}</option>)}
      </select></label>
      <label>{t("status")} <select className="rounded border bg-surface p-2" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}>
        <option value="">{t("all")}</option>{["pending", "completed", "failed", "cancelled", "interrupted", "invalid", "truncated"].map(value => <option key={value}>{value}</option>)}
      </select></label>
      {([["model", model, setModel], ["operation", operation, setOperation], ["from", from, setFrom], ["to", to, setTo]] as const).map(([key, value, setter]) =>
        <label key={key}>{t(key)} <input className="rounded border bg-surface p-2" type={key === "from" || key === "to" ? "date" : "text"} value={value} onChange={e => { setter(e.target.value); setOffset(0); }} /></label>)}
    </div>
    {error && <p role="alert">{t("error")}</p>}
    {isLoading && <p role="status">{t("loading")}</p>}
    {data && <>
      <dl className="grid gap-4 sm:grid-cols-3">
        <div><dt>{t("calls")}</dt><dd className="text-xl">{data.summary.calls}</dd></div>
        <div><dt>{t("tokens")}</dt><dd className="text-xl">{data.summary.input_tokens ?? "—"} / {data.summary.output_tokens ?? "—"}</dd></div>
        <div><dt>{t("cost")}</dt><dd className="text-xl">{money(data.summary.known_cost_usd)}</dd></div>
      </dl>
      <p className="text-sm text-fg-muted">{t("incomplete", { usage: data.summary.unknown_usage_calls, cost: data.summary.unknown_cost_calls, pending: data.summary.pending_calls })}</p>
      <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>
        {["time", "service", "model", "status", "tokens", "cost"].map(key => <th className="p-2" key={key}>{t(key)}</th>)}
      </tr></thead><tbody>{data.items.map(row => <tr className="border-t" key={row.call_id}>
        <td className="p-2">{new Date(row.created_at).toISOString()}</td><td className="p-2">{row.service}<div className="text-xs text-fg-muted">{row.stage}</div></td>
        <td className="p-2">{row.model ?? "—"}</td><td className="p-2">{row.status}</td>
        <td className="p-2">{row.input_tokens ?? "—"} / {row.output_tokens ?? "—"}</td><td className="p-2">{money(row.estimated_cost_usd)}</td>
      </tr>)}</tbody></table></div>
      {!data.items.length && <p>{t("empty")}</p>}
      <div className="flex gap-4"><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>{t("previous")}</button><button disabled={data.nextOffset === null} onClick={() => setOffset(data.nextOffset ?? offset)}>{t("next")}</button></div>
      <p className="text-sm text-fg-muted">{t("limits")}</p>
    </>}
  </section>;
}
