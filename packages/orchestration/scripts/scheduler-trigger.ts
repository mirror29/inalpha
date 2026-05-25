/**
 * Scheduler CLI 手动触发 —— 跳过 cron，立即执行一次指定 job。
 *
 * 用法：
 *
 *   pnpm scheduler:trigger <job_id>           # 跑一个 job 并打印结果
 *   pnpm scheduler:trigger --list             # 列出全部 jobs
 *
 * 何时用：
 *
 * - 本地 dev 验证 job 配置正确
 * - 故障恢复（cron 错过窗口想立刻补一次）
 * - smoke test（CI 跑通整条链路）
 *
 * 何时不用：
 *
 * - 不要在生产长期用此 CLI 替代 cron（会绕过 advisory lock）
 *
 * 坑：本脚本直接调 runJob，不走 cron 路径 —— 也就是说 trigger 字段会标 'manual'。
 *      因为不在 mastra dev 进程内，agent mode 调 generate 会另起 LLM 调用，可能耗时较长。
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

const envPath = resolve(process.cwd(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { mastra } from "../src/mastra/index.js";
import { closePool, getJob, listAllJobs } from "../src/scheduler/repo.js";
import { runJob } from "../src/scheduler/runner.js";

async function main(): Promise<void> {
  const args = process.argv.slice(2);

  if (args.includes("--list") || args.length === 0) {
    const jobs = await listAllJobs();
    console.log(`scheduler 共 ${jobs.length} 个 job：\n`);
    for (const j of jobs) {
      const state = j.enabled ? "ENABLED " : "disabled";
      console.log(
        `  [${state}] ${j.jobId.padEnd(30)} cron='${j.cronExpr}' tz=${j.timezone} mode=${j.mode}`,
      );
    }
    if (args.length === 0) {
      console.log("\n用法：pnpm scheduler:trigger <job_id>");
    }
    return;
  }

  const jobId = args[0]!;
  const job = await getJob(jobId);
  if (job === null) {
    console.error(`✗ job ${jobId} 不存在`);
    process.exitCode = 2;
    return;
  }

  console.log(`─── 触发 ${jobId} (${job.mode}) ───`);
  const result = await runJob({
    job,
    mastra,
    scheduledAt: new Date(),
    trigger: "manual",
  });
  console.log(JSON.stringify(result, null, 2));
  if (result.status !== "success") {
    process.exitCode = 1;
  }
}

main()
  .catch((err: unknown) => {
    console.error("✗ scheduler-trigger 异常：");
    console.error(err);
    process.exitCode = 1;
  })
  .finally(() => {
    void closePool();
  });
