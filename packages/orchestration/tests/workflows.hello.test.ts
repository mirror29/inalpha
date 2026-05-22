/**
 * Hello-world spike workflow 验证（ADR-0025 D3 pre-step）。
 *
 * 验证 Mastra 1.36 这 6 件事：
 *
 *   1. createWorkflow / createStep API 签名能编译
 *   2. .then() 串联：上游 output 自动喂下游 input
 *   3. .foreach({ concurrency }) 真并发（用 worker_marker 唯一数 + 墙钟时间双重证）
 *   4. .commit() 收尾 + 注册到 Mastra 实例
 *   5. mastra.getWorkflow('id').createRun() 取 workflow + 起 run
 *   6. run.start({ inputData }) 返 discriminated union（success / failed 走不同分支）
 */
import { describe, expect, it } from "vitest";

import { mastra } from "../src/mastra/index.js";

describe("hello_spike workflow (ADR-0025 API spike)", () => {
  it("returns success with full pipeline output (validates #1, #2, #4, #5, #6)", async () => {
    const wf = mastra.getWorkflow("hello_spike");
    expect(wf).toBeDefined();

    const run = await wf.createRun();
    const result = await run.start({ inputData: { n: 5 } });

    expect(result.status).toBe("success");
    if (result.status !== "success") return; // narrow for TS

    expect(result.result.total).toBe(5);
    expect(result.result.joined).toBe(
      "item-0-item-0,item-1-item-1,item-2-item-2,item-3-item-3,item-4-item-4",
    );
    // unique_workers 至少 1（每个 step run 都生成新 marker）；并发观察留下一个测试
    expect(result.result.unique_workers).toBeGreaterThanOrEqual(1);
  });

  it("foreach actually parallelizes (validates #3 — wall-time check)", async () => {
    const wf = mastra.getWorkflow("hello_spike");
    const run = await wf.createRun();

    // 9 个 item，每个 sleep 20ms；concurrency=3 应 wall ≈ 3 batches × 20ms = 60ms+
    // 串行的话是 9 × 20ms = 180ms+
    // 留余量：判 < 150ms 即认为并发了
    const t0 = Date.now();
    const result = await run.start({ inputData: { n: 9 } });
    const elapsed = Date.now() - t0;

    expect(result.status).toBe("success");
    expect(elapsed).toBeLessThan(150);
  });

  it("step output flows into next step (validates #2 explicitly)", async () => {
    const wf = mastra.getWorkflow("hello_spike");
    const run = await wf.createRun();
    const result = await run.start({ inputData: { n: 2 } });

    expect(result.status).toBe("success");
    if (result.status !== "success") return;

    // aggregate 收到 doubleStep 输出（item-0-item-0 / item-1-item-1）
    expect(result.result.joined).toBe("item-0-item-0,item-1-item-1");
    expect(result.result.total).toBe(2);
  });

  it("input zod validation rejects invalid input (validates schema enforcement)", async () => {
    const wf = mastra.getWorkflow("hello_spike");
    const run = await wf.createRun();

    // n=20 超 max(10) → workflow 内部要么 status:'failed' 要么 throw
    let caught: unknown = null;
    let res: Awaited<ReturnType<typeof run.start>> | null = null;
    try {
      res = await run.start({ inputData: { n: 20 } });
    } catch (e) {
      caught = e;
    }

    // 任一形式都行：不能静默返 success
    expect(caught !== null || (res !== null && res.status !== "success")).toBe(true);
  });
});
