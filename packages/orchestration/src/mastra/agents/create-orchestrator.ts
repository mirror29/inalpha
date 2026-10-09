import { Agent, type AgentConfig } from "@mastra/core/agent";
import { TokenLimiterProcessor, type InputProcessor } from "@mastra/core/processors";

import { buildInstructions } from "./instructions/index.js";

type BaseAgentConfig = AgentConfig<string>;

/** 创建 Orchestrator 时必须显式提供的依赖。 */
export type CreateOrchestratorOptions = Pick<
  BaseAgentConfig,
  "model" | "tools"
> & {
  /** 可选 memory；评测省略时保持完全无状态。 */
  memory?: BaseAgentConfig["memory"];
  /** 可选输入处理器；生产用量账本由入口注入。 */
  inputProcessors?: InputProcessor[];
  /** 可选输出处理器；评测省略时不连接后端服务。 */
  outputProcessors?: BaseAgentConfig["outputProcessors"];
  /** 单次 Agent 循环的最大步骤数。 */
  maxSteps?: number;
};

/**
 * 创建依赖显式、可隔离运行的 Orchestrator。
 *
 * 本模块不得加载 provider、MCP、持久 memory 或服务客户端；生产入口负责注入
 * 这些依赖，离线评测则注入 scripted model 与 synthetic tools。
 */
export function createOrchestrator(options: CreateOrchestratorOptions): Agent {
  return new Agent({
    id: "orchestrator",
    name: "orchestrator",
    instructions: buildInstructions,
    model: options.model,
    maxRetries: 0,
    tools: options.tools,
    ...(options.memory === undefined ? {} : { memory: options.memory }),
    inputProcessors: [
      new TokenLimiterProcessor({ limit: 500_000, trimMode: "contiguous" }),
      ...(options.inputProcessors ?? []),
    ],
    ...(options.outputProcessors === undefined
      ? {}
      : { outputProcessors: options.outputProcessors }),
    defaultOptions: {
      maxSteps: options.maxSteps ?? 40,
    },
  });
}
