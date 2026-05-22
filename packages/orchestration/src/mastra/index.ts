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
import { PinoLogger } from "@mastra/loggers";

import { orchestrator } from "./agents/orchestrator.js";
import { helloSpikeWorkflow } from "./workflows/_hello.js";

export const mastra = new Mastra({
  // D-8a'：只剩 orchestrator 一个 agent；trader/risk subagent 已废弃
  // 安全护栏从"agent prompt + tool 集隔离"下沉到"plan store + permissions deny"
  agents: { orchestrator },
  // ADR-0025 spike：hello_spike 验证 Mastra 1.36 workflow API（动 swarm 前先确认 API 没变）
  workflows: { hello_spike: helloSpikeWorkflow },
  logger: new PinoLogger({ name: "inalpha", level: "info" }),
});
