/**
 * Hello-world workflow —— Mastra 1.36 API 验证 spike（ADR-0025 D3 pre-step）。
 *
 * 验证目标（顺利则继续 swarm 实施；失败则 ADR-0025 D3 章节要返修）：
 *
 * 1. ``createWorkflow`` / ``createStep`` 签名 + Zod schema 推导
 * 2. ``.then(step)`` 串联 + 上游 output 自动喂下游 input
 * 3. ``.foreach(step, { concurrency: N })`` 真正并发（前步必须输出 array）
 * 4. ``.commit()`` 收尾
 * 5. ``mastra.getWorkflow('hello_spike').createRun()`` 取 + 跑
 * 6. ``run.start({ inputData })`` 返 discriminated union（success / failed）
 *
 * 在 tests/workflows.hello.test.ts 里有 e2e 断言；本文件只放 workflow 定义。
 *
 * 任务完成后这俩文件会留着（作活的 API 参考），swarm workflow 在隔壁 ``backtest-grid.ts``
 * 复用同一 pattern。
 */
import { createStep, createWorkflow } from "@mastra/core/workflows";
import { z } from "zod";

// ─── schemas ───────────────────────────────────────────────────────

const HelloInputSchema = z.object({
  n: z.number().int().min(1).max(10).describe("展开多少个 item 给 foreach"),
});

const ItemSchema = z.object({
  index: z.number().int(),
  value: z.string(),
});

const DoubledSchema = z.object({
  index: z.number().int(),
  doubled: z.string(),
  worker_marker: z.string().describe("仅用来验证并发：含随机 token，不同 step 间不同"),
});

const HelloOutputSchema = z.object({
  total: z.number().int(),
  joined: z.string(),
  unique_workers: z.number().int(),
});

// ─── steps ─────────────────────────────────────────────────────────

const expandStep = createStep({
  id: "expand",
  inputSchema: HelloInputSchema,
  outputSchema: z.array(ItemSchema),
  execute: async ({ inputData }) => {
    return Array.from({ length: inputData.n }, (_, i) => ({
      index: i,
      value: `item-${i}`,
    }));
  },
});

const doubleStep = createStep({
  id: "double",
  inputSchema: ItemSchema,
  outputSchema: DoubledSchema,
  execute: async ({ inputData }) => {
    // 故意 sleep 让并发可观测
    await new Promise((r) => setTimeout(r, 20));
    return {
      index: inputData.index,
      doubled: `${inputData.value}-${inputData.value}`,
      worker_marker: Math.random().toString(36).slice(2, 10),
    };
  },
});

const aggregateStep = createStep({
  id: "aggregate",
  inputSchema: z.array(DoubledSchema),
  outputSchema: HelloOutputSchema,
  execute: async ({ inputData }) => {
    const joined = inputData.map((d) => d.doubled).join(",");
    const unique_workers = new Set(inputData.map((d) => d.worker_marker)).size;
    return { total: inputData.length, joined, unique_workers };
  },
});

// ─── workflow ──────────────────────────────────────────────────────

export const helloSpikeWorkflow = createWorkflow({
  id: "hello_spike",
  inputSchema: HelloInputSchema,
  outputSchema: HelloOutputSchema,
})
  .then(expandStep)
  .foreach(doubleStep, { concurrency: 3 })
  .then(aggregateStep)
  .commit();
