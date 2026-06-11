"use client";

import { useMemo } from "react";

import { cn } from "@/lib/cn";

import { compact, shortTimestamp } from "./tool-views/format";

/**
 * 工具输出的结构化可视化 —— 按 JSON 形态自适应渲染,替代裸 JSON 文本:
 *  - 对象 → 键值行(嵌套对象/数组折叠成 <details>,带条目数提示)
 *  - 对象数组 → 紧凑表格(K 线 / run_log / 因子列表等最常见形态)
 *  - 标量数组 → 内联 chip 列表
 *  - 长文本 / 多行字符串(策略代码等)→ 折叠块,展开看全文
 *  - 标量格式化:ISO 时间戳缩短、boolean 中性区分(true 实色/false 弱化,红色只留给 error)、null/空串区分
 *  - mastra 工具错误封套 {isError, output} → 红色 ERROR 标头 + 只展开 output
 *
 * 解析失败(非 JSON)原样显示纯文本。容量护栏:深度 / 表格行列 / chip 数均有上限,
 * 截断处显式标注剩余条数 —— 完整数据由 ToolChip 的 raw 切换兜底。
 */
export function ToolOutput({ raw }: { raw: string }) {
  const parsed = useMemo(() => {
    try {
      return JSON.parse(raw) as unknown;
    } catch {
      return UNPARSEABLE;
    }
  }, [raw]);

  if (parsed === UNPARSEABLE) {
    return (
      <pre className="max-h-64 overflow-auto px-2.5 py-1.5 font-mono text-[11px] leading-relaxed text-fg-muted">
        {raw}
      </pre>
    );
  }

  // mastra 工具报错封套:红标头 + 直接展开 output(不让用户先点开一层 isError)。
  if (isErrorEnvelope(parsed)) {
    return (
      <div className="max-h-64 overflow-auto px-2.5 py-1.5">
        <div className="mb-1 inline-block rounded-sm border border-fox-red/40 bg-fox-red/10 px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.18em] text-fox-red">
          error
        </div>
        <JsonNode value={parsed.output} depth={0} />
      </div>
    );
  }

  return (
    <div className="max-h-64 overflow-auto px-2.5 py-1.5">
      <JsonNode value={parsed} depth={0} />
    </div>
  );
}

/** JSON.parse 失败的哨兵(结果本身可能是 null/false,不能用它们当失败标记)。 */
const UNPARSEABLE = Symbol("unparseable");

const MAX_DEPTH = 5;
const TABLE_ROW_CAP = 20;
const TABLE_COL_CAP = 6;
const CHIP_CAP = 30;
const LONG_TEXT_THRESHOLD = 160;

function isErrorEnvelope(
  v: unknown,
): v is { isError: true; output: unknown } {
  return (
    !!v &&
    typeof v === "object" &&
    (v as { isError?: unknown }).isError === true &&
    "output" in v
  );
}

/** 形态分发:数组 / 对象 / 标量。深度超限退回紧凑 JSON 文本。 */
function JsonNode({ value, depth }: { value: unknown; depth: number }) {
  if (depth > MAX_DEPTH) {
    return (
      <span className="break-all font-mono text-[11px] text-fg-muted/70">
        {compact(value)}
      </span>
    );
  }
  if (Array.isArray(value)) return <ArrayNode value={value} depth={depth} />;
  if (value && typeof value === "object")
    return <ObjectNode value={value as Record<string, unknown>} depth={depth} />;
  return <Scalar value={value} />;
}

/** 对象 → 键值行;嵌套容器折叠,空容器原位显示。 */
function ObjectNode({
  value,
  depth,
}: {
  value: Record<string, unknown>;
  depth: number;
}) {
  const entries = Object.entries(value);
  if (entries.length === 0)
    return <span className="font-mono text-[11px] text-fg-muted/50">{"{}"}</span>;
  return (
    <div className="flex flex-col gap-0.5">
      {entries.map(([k, v]) => (
        <ObjectRow key={k} k={k} v={v} depth={depth} />
      ))}
    </div>
  );
}

function ObjectRow({ k, v, depth }: { k: string; v: unknown; depth: number }) {
  const keyEl = (
    <span className="shrink-0 break-all font-mono text-[11px] text-fg-muted/60">
      {k}
    </span>
  );

  // 嵌套容器(非空)→ 折叠块,summary 带形态提示(N 项 / N 字段)。
  if (isNonEmptyContainer(v)) {
    return (
      <details className="group/nest min-w-0">
        <summary className="flex cursor-pointer items-baseline gap-2 rounded-sm py-px hover:bg-bg-elev/40">
          {keyEl}
          <span className="font-mono text-[10px] text-fg-muted/40 transition-colors group-open/nest:text-cyan/60">
            {Array.isArray(v) ? `[${v.length}]` : `{${Object.keys(v as object).length}}`}
          </span>
        </summary>
        <div className="ml-2 border-l border-border-subtle/60 pl-2 pt-0.5">
          <JsonNode value={v} depth={depth + 1} />
        </div>
      </details>
    );
  }

  // 长文本(策略代码 / 日志)→ 单独折叠块。
  if (typeof v === "string" && isLongText(v)) {
    return (
      <details className="min-w-0">
        <summary className="flex cursor-pointer items-baseline gap-2 rounded-sm py-px hover:bg-bg-elev/40">
          {keyEl}
          <span className="min-w-0 truncate font-mono text-[11px] text-fg-muted/70">
            {firstLine(v)}
          </span>
        </summary>
        <pre className="ml-2 mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap break-all border-l border-border-subtle/60 pl-2 font-mono text-[11px] leading-relaxed text-fg-muted">
          {v}
        </pre>
      </details>
    );
  }

  return (
    <div className="flex min-w-0 items-baseline gap-2 py-px">
      {keyEl}
      <span className="min-w-0 break-words text-right ml-auto">
        <Scalar value={v} />
      </span>
    </div>
  );
}

/** 数组:全标量 → chip 列;对象数组 → 表格;混合 → 逐项折叠。 */
function ArrayNode({ value, depth }: { value: unknown[]; depth: number }) {
  if (value.length === 0)
    return <span className="font-mono text-[11px] text-fg-muted/50">[]</span>;

  if (value.every((v) => !v || typeof v !== "object")) {
    const shown = value.slice(0, CHIP_CAP);
    return (
      <div className="flex flex-wrap items-center gap-1 py-0.5">
        {shown.map((v, i) => (
          <span
            key={i}
            className="rounded-sm border border-border-subtle/70 bg-bg/40 px-1 py-px"
          >
            <Scalar value={v} />
          </span>
        ))}
        {value.length > shown.length && (
          <MoreHint n={value.length - shown.length} />
        )}
      </div>
    );
  }

  if (isUniformObjectArray(value)) {
    return <ObjectTable rows={value} />;
  }

  // 混合 / 异构数组:逐项折叠。
  return (
    <div className="flex flex-col gap-0.5">
      {value.slice(0, TABLE_ROW_CAP).map((v, i) => (
        <ObjectRow key={i} k={`[${i}]`} v={v} depth={depth} />
      ))}
      {value.length > TABLE_ROW_CAP && (
        <MoreHint n={value.length - TABLE_ROW_CAP} />
      )}
    </div>
  );
}

/** 数组元素是否全为(可做表格的)普通对象。 */
function isUniformObjectArray(v: unknown[]): v is Record<string, unknown>[] {
  return v.every(
    (x) => !!x && typeof x === "object" && !Array.isArray(x),
  );
}

/**
 * 对象数组 → 紧凑表格。列按信息量排序后取前 N:
 * 值有变化的列(K 线的 OHLC / 时间)优先于全列相同的常量列(venue / symbol),
 * 同档再按出现频率;行超限标注剩余。
 */
function ObjectTable({ rows }: { rows: Record<string, unknown>[] }) {
  const sample = rows.slice(0, 10);
  const freq = new Map<string, number>();
  const distinct = new Map<string, Set<string>>();
  for (const r of sample)
    for (const [k, v] of Object.entries(r)) {
      freq.set(k, (freq.get(k) ?? 0) + 1);
      const set = distinct.get(k) ?? new Set<string>();
      set.add(compact(v, 60));
      distinct.set(k, set);
    }
  const varies = (k: string) =>
    sample.length > 1 && (distinct.get(k)?.size ?? 0) > 1 ? 0 : 1;
  const cols = [...freq.entries()]
    .sort((a, b) => varies(a[0]) - varies(b[0]) || b[1] - a[1])
    .slice(0, TABLE_COL_CAP)
    .map(([k]) => k);
  const hiddenCols = freq.size - cols.length;
  const shown = rows.slice(0, TABLE_ROW_CAP);

  return (
    <div className="overflow-x-auto py-0.5">
      <table className="w-full border-collapse font-mono text-[11px]">
        <thead>
          <tr>
            {cols.map((c) => (
              <th
                key={c}
                className="whitespace-nowrap border-b border-border-subtle px-1.5 py-0.5 text-left font-normal uppercase tracking-wider text-fg-muted/50"
              >
                {c}
              </th>
            ))}
            {hiddenCols > 0 && (
              <th className="whitespace-nowrap border-b border-border-subtle px-1.5 py-0.5 text-left font-normal text-fg-muted/40">
                +{hiddenCols}
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i} className="border-b border-border-subtle/40 last:border-b-0">
              {cols.map((c) => (
                <td
                  key={c}
                  className="max-w-40 truncate whitespace-nowrap px-1.5 py-0.5"
                  title={cellTitle(r[c])}
                >
                  {isNonEmptyContainer(r[c]) ? (
                    <span className="text-fg-muted/60">{compact(r[c], 40)}</span>
                  ) : (
                    <Scalar value={r[c]} />
                  )}
                </td>
              ))}
              {hiddenCols > 0 && <td className="px-1.5 py-0.5 text-fg-muted/30">…</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > shown.length && <MoreHint n={rows.length - shown.length} />}
    </div>
  );
}

/** 标量格式化:时间戳缩短 / boolean 着色 / null·空串区分 / 数字 tabular。 */
function Scalar({ value }: { value: unknown }) {
  if (value === null || value === undefined)
    return <span className="font-mono text-[11px] text-fg-muted/40">—</span>;
  // boolean 中性区分（true 实色 / false 弱化），不用红绿——这里没有字段语义，
  // truncated:false / fresh:false 都是正常态，红色会被误读成报错；红色只留给 error 封套。
  if (typeof value === "boolean")
    return (
      <span
        className={cn(
          "font-mono text-[11px]",
          value ? "text-cyan/80" : "text-fg-muted/60",
        )}
      >
        {String(value)}
      </span>
    );
  if (typeof value === "number")
    return (
      <span className="font-mono text-[11px] tabular-nums text-fg">
        {value}
      </span>
    );
  const s = String(value);
  if (s === "")
    return <span className="font-mono text-[11px] text-fg-muted/40">&quot;&quot;</span>;
  const ts = shortTimestamp(s);
  if (ts)
    return (
      <span title={s} className="font-mono text-[11px] tabular-nums text-fg-muted">
        {ts}
      </span>
    );
  return <span className="break-all font-mono text-[11px] text-fg">{s}</span>;
}

function MoreHint({ n }: { n: number }) {
  return (
    <div className="py-0.5 font-mono text-[10px] text-fg-muted/40">+{n} …</div>
  );
}

function isNonEmptyContainer(v: unknown): boolean {
  if (Array.isArray(v)) return v.length > 0;
  if (v && typeof v === "object") return Object.keys(v).length > 0;
  return false;
}

function isLongText(s: string): boolean {
  return s.includes("\n") || s.length > LONG_TEXT_THRESHOLD;
}

function firstLine(s: string): string {
  const line = s.split("\n", 1)[0];
  return line.length > 60 ? `${line.slice(0, 60)}…` : line;
}

/** 表格单元格悬浮提示:完整值(容器转紧凑 JSON,标量原样)。 */
function cellTitle(v: unknown): string {
  if (v === null || v === undefined) return "";
  return typeof v === "object" ? compact(v, 300) : String(v);
}
