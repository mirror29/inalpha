/**
 * Mastra LLM provider 工厂 —— 按 `LLM_PROVIDER` env 选 7 家 provider 之一。
 *
 * 与 Python `services/research/llm/client.py` 的 `build_llm_client` 对称：
 * 同一 `LLM_PROVIDER` 值在两边都能 work，便于统一切换 provider。
 *
 * 支持的 provider（与 README §Quick Start 对齐）：
 *
 * - **OpenAI-compat 家族**（走 `@ai-sdk/openai-compatible` 或专属 sdk）：
 *   `deepseek` / `openai` / `kimi` / `zhipu` / `ollama`
 * - **原生 SDK**：`anthropic` / `gemini`
 *
 * 调用方应在 import 列表最前面 `import "../../env.js"`，确保 dotenv 已加载。
 */
import { createAnthropic } from "@ai-sdk/anthropic";
import { createDeepSeek } from "@ai-sdk/deepseek";
import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createOpenAI } from "@ai-sdk/openai";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import type { LanguageModel } from "@mastra/core/llm";

export type LLMProvider =
  | "deepseek"
  | "anthropic"
  | "openai"
  | "gemini"
  | "kimi"
  | "zhipu"
  | "ollama";

export const SUPPORTED_PROVIDERS: readonly LLMProvider[] = [
  "deepseek",
  "anthropic",
  "openai",
  "gemini",
  "kimi",
  "zhipu",
  "ollama",
] as const;

/** 各 provider 默认模型 —— 用户在 `.env` 留空 `LLM_MODEL` 时生效。
 *
 * 选型原则（2026-05 更新，每家**当前主流旗舰**）。开 `.env LLM_MODEL=...` 覆盖
 * 即可换 reasoning / cheap 变体。
 *
 * | provider | model | 说明 |
 * |---|---|---|
 * | deepseek | deepseek-v4-pro | V4 主旗舰（1.6T MoE）；老 alias deepseek-chat 将在 2026-07-24 下线 |
 * | anthropic | claude-opus-4-7 | Opus 4.7 旗舰（2026-04 发布）；省钱用 claude-sonnet-4-6 / haiku-4-5 |
 * | openai | gpt-5.5 | 最新 frontier；快速用 gpt-5.4-mini / gpt-5.4-nano；reasoning 走 gpt-5.2-pro |
 * | gemini | gemini-3-pro | Gemini 3 Pro（2025-11 GA，替代 2.5 Pro）；快速廉价用 gemini-3.5-flash |
 * | kimi | kimi-k2.6 | K2.6（2026-04-20 发布）；老 kimi-k2-0905 将在 2026-05-25 下线 |
 * | zhipu | glm-5.1 | GLM-5 系列旗舰；轻量用 glm-4.5-air |
 * | ollama | llama4 | Llama 4 默认 tag = Scout（17B/109B MoE）；大显存用 llama4:128x17b（Maverick）|
 */
const DEFAULT_MODELS: Record<LLMProvider, string> = {
  deepseek: "deepseek-v4-pro",
  anthropic: "claude-opus-4-7",
  openai: "gpt-5.5",
  gemini: "gemini-3-pro",
  kimi: "kimi-k2.6",
  zhipu: "glm-5.1",
  ollama: "llama4",
};

// OpenAI-compat provider 的 base URL —— 各 vendor 文档固定值
// ollama 单独走 OLLAMA_BASE_URL env（默认 http://localhost:11434/v1）
const KIMI_BASE_URL = "https://api.moonshot.cn/v1";
const ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4";

function requireKey(envName: string, provider: string): string {
  const v = process.env[envName];
  if (!v || v.trim() === "") {
    throw new Error(
      `${provider}: ${envName} is missing. 在根目录 .env 填入对应 key 后重启。`,
    );
  }
  return v;
}

/**
 * 按 `LLM_PROVIDER` env 构造 Mastra `LanguageModel`。
 *
 * @example
 *   // .env: LLM_PROVIDER=anthropic  LLM_MODEL=claude-opus-4-7  ANTHROPIC_API_KEY=sk-...
 *   const model = buildLLM();
 *   new Agent({ id, instructions, model, tools });
 */
export function buildLLM(): LanguageModel {
  const providerRaw = (process.env.LLM_PROVIDER ?? "deepseek").toLowerCase();
  if (!SUPPORTED_PROVIDERS.includes(providerRaw as LLMProvider)) {
    throw new Error(
      `Unknown LLM_PROVIDER "${providerRaw}"; supported: ${SUPPORTED_PROVIDERS.join(" | ")}`,
    );
  }
  const provider = providerRaw as LLMProvider;
  const model = process.env.LLM_MODEL?.trim() || DEFAULT_MODELS[provider];

  // 所有 ai-sdk provider 返回的 LanguageModelVN 与 mastra-core 期望的
  // MastraLanguageModelVN 存在 doGenerate/doStream 返回类型微差（stream prop
  // 在两侧定义不一致）；运行时 mastra-core 会动态适配，type 用 cast 绕开。
  // 等 mastra-core 与 ai-sdk 版本对齐后再去掉断言。
  switch (provider) {
    case "deepseek":
      return createDeepSeek({
        apiKey: requireKey("DEEPSEEK_API_KEY", "deepseek"),
      })(model) as unknown as LanguageModel;

    case "anthropic":
      return createAnthropic({
        apiKey: requireKey("ANTHROPIC_API_KEY", "anthropic"),
      })(model) as unknown as LanguageModel;

    case "openai":
      return createOpenAI({
        apiKey: requireKey("OPENAI_API_KEY", "openai"),
      })(model) as unknown as LanguageModel;

    case "gemini":
      return createGoogleGenerativeAI({
        apiKey: requireKey("GEMINI_API_KEY", "gemini"),
      })(model) as unknown as LanguageModel;

    case "kimi":
      return createOpenAICompatible({
        name: "kimi",
        apiKey: requireKey("KIMI_API_KEY", "kimi"),
        baseURL: KIMI_BASE_URL,
      })(model) as unknown as LanguageModel;

    case "zhipu":
      return createOpenAICompatible({
        name: "zhipu",
        apiKey: requireKey("ZHIPU_API_KEY", "zhipu"),
        baseURL: ZHIPU_BASE_URL,
      })(model) as unknown as LanguageModel;

    case "ollama":
      return createOpenAICompatible({
        name: "ollama",
        // ollama 默认不需要 key，但 sdk 要求传一个非空字符串
        apiKey: process.env.OLLAMA_API_KEY || "ollama",
        baseURL: process.env.OLLAMA_BASE_URL?.trim() || "http://localhost:11434/v1",
      })(model) as unknown as LanguageModel;
  }
}
