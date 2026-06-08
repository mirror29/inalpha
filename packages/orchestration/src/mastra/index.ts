/**
 * Mastra 实例 —— ``mastra dev`` 自动发现的入口。
 *
 * 启动：
 *
 *   pnpm dev           # http://localhost:4111 playground
 *
 * 需要：
 *
 * - services/data + services/paper 都在跑（D-3 / D-6）
 * - DEEPSEEK_API_KEY 在 .env
 * - JWT_SECRET 跟 services 一致
 */
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { loadEnvFile } from "node:process";

// dev 启动时显式加载 .env（mastra CLI 的 cwd 是 package root）
const envPath = resolve(process.cwd(), ".env");
if (existsSync(envPath)) {
  loadEnvFile(envPath);
}

import { Mastra } from "@mastra/core/mastra";
import { LibSQLStore } from "@mastra/libsql";
import { PinoLogger } from "@mastra/loggers";
import {
  ConsoleExporter,
  MastraStorageExporter,
  Observability,
  SamplingStrategyType,
} from "@mastra/observability";

import { getSettings } from "../config.js";
import { divinationApiRoutes } from "../divination/api.js";
import { permissionsApiRoutes } from "../permissions/api.js";
import { schedulerApiRoutes } from "../scheduler/api.js";
import { bootstrapScheduler } from "../scheduler/index.js";
import { orchestrator } from "./agents/orchestrator.js";
import { helloSpikeWorkflow } from "./workflows/_hello.js";
import { backtestGridWorkflow } from "./workflows/backtest-grid.js";

// D-9：observability storage —— traces UI tab 必需。LibSQL file DB 零运维。
// `.mastra/inalpha.db` 落到 mastra build 目录，dev 启停不污染仓库。
const observabilityStore = new LibSQLStore({
  id: "inalpha-traces",
  url: "file:.mastra/inalpha-traces.db",
});

// D-9：observability —— 追踪 agent / tool / workflow 全链路。
// 双 exporter：ConsoleExporter（stdout 实时打印 span）+ MastraStorageExporter（落 storage
// 让 playground "Traces" tab 能加载历史）。prod 可换 OTLP exporter → Jaeger / Tempo。
const observability = new Observability({
  configs: {
    default: {
      serviceName: "inalpha-orchestration",
      sampling: { type: SamplingStrategyType.ALWAYS },
      exporters: [new ConsoleExporter(), new MastraStorageExporter()],
    },
  },
});

export const mastra = new Mastra({
  storage: observabilityStore,
  // D-8a'：只剩 orchestrator 一个 agent；trader/risk subagent 已废弃
  // 安全护栏从"agent prompt + tool 集隔离"下沉到"plan store + permissions deny"
  agents: { orchestrator },
  // ADR-0025 spike：hello_spike 验证 Mastra 1.36 workflow API（保留作为活的 API 参考）
  // ADR-0025 §D3：backtest_grid Swarm S1
  workflows: {
    hello_spike: helloSpikeWorkflow,
    backtest_grid: backtestGridWorkflow,
  },
  logger: new PinoLogger({ name: "inalpha", level: "info" }),
  observability,
  // D-9：scheduler HTTP 管理面 + D-9.1b：permissions ask 审批通道
  // 两套路由共用 4111 端口
  //
  // timeout：Mastra 网关请求超时（ms）。默认 180s 会在 research.deep_dive 长任务
  // （多 analyst + persona 大师团 + 辩论，单次可达 3-5min）未返回前就 504 掉，agent
  // 白等。设 600s 让网关 ≥ ResearchClient 自身 300s 超时 —— 网关不会比工具更早掐断
  // （ADR-0037 调试记录）。
  server: {
    timeout: 600_000,
    apiRoutes: [...schedulerApiRoutes, ...permissionsApiRoutes, ...divinationApiRoutes],
  },
});

// D-9：类 Hermes 定时 agent 模式。默认关闭，需在 .env 设 `SCHEDULER_ENABLED=true` 才启动。
// 避免本地 dev 反复触发污染 paper 账户；进程退出时 SIGTERM/SIGINT hook 自动释放 advisory lock。
if (getSettings().schedulerEnabled) {
  bootstrapScheduler(mastra);
}
