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
 * env 加载：本模块顶层显式调 `ensureEnvLoaded()`——不能依赖调用方的
 * side-effect `import "../../env.js"`（mastra bundler 会丢纯副作用 import，
 * 见 env.ts 注释，2026-06-11 实测）。
 */
import { AsyncLocalStorage } from "node:async_hooks";

import { createAnthropic } from "@ai-sdk/anthropic";
import { createDeepSeek } from "@ai-sdk/deepseek";
import { createGoogleGenerativeAI } from "@ai-sdk/google";
import { createOpenAI } from "@ai-sdk/openai";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import type { LanguageModel } from "@mastra/core/llm";

import { ensureEnvLoaded } from "../../env.js";

ensureEnvLoaded();

/**
 * 是否启用多用户登录（从 AUTH_ENABLED env 读取）。
 * 启用后，用户必须配置自己的 API Key，不会 fallback 到系统级配置。
 */
export const AUTH_ENABLED = process.env.AUTH_ENABLED === "true";

/**
 * Per-request user LLM config store（AsyncLocalStorage）。
 *
 * identity middleware 解析 X-LLM-Config header 后写入此处，
 * buildUserAwareModel() 在 doGenerate/doStream 时读取，按用户配置
 * 动态构造 LanguageModel；无配置时降级到系统级 buildLLM()。
 */
export const userLLMStore = new AsyncLocalStorage<UserLLMConfig>();

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
 * | anthropic | claude-opus-4-8 | Opus 4.8 旗舰；省钱用 claude-sonnet-4-6 / haiku-4-5 |
 * | openai | gpt-5.5 | 最新 frontier；快速用 gpt-5.4-mini / gpt-5.4-nano；reasoning 走 gpt-5.2-pro |
 * | gemini | gemini-3-pro | Gemini 3 Pro（2025-11 GA，替代 2.5 Pro）；快速廉价用 gemini-3.5-flash |
 * | kimi | kimi-k2.6 | K2.6（2026-04-20 发布）；老 kimi-k2-0905 将在 2026-05-25 下线 |
 * | zhipu | glm-5.2 | GLM-5 系列旗舰；轻量用 glm-4.5-air |
 * | ollama | llama4 | Llama 4 默认 tag = Scout（17B/109B MoE）；大显存用 llama4:128x17b（Maverick）|
 */
const DEFAULT_MODELS: Record<LLMProvider, string> = {
  deepseek: "deepseek-v4-pro",
  anthropic: "claude-opus-4-8",
  openai: "gpt-5.5",
  gemini: "gemini-3-pro",
  kimi: "kimi-k2.6",
  zhipu: "glm-5.2",
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
 * 用户 LLM 配置（从 dashboard 传入）。
 * 与 apps/dashboard/src/lib/user-preferences.ts 对齐。
 */
export interface UserLLMConfig {
  id: string;
  provider: "deepseek" | "anthropic" | "openai" | "gemini" | "kimi" | "zhipu" | "custom";
  model?: string;
  api_key: string; // 解密后的明文
  custom_base_url?: string;
  custom_provider_name?: string;
  label?: string;
}

/**
 * 预设供应商默认端点（与 dashboard 对齐）。
 */
const PROVIDER_BASE_URLS: Partial<Record<string, string>> = {
  deepseek: "https://api.deepseek.com",
  openai: "https://api.openai.com/v1",
  kimi: "https://api.moonshot.cn/v1",
  zhipu: "https://open.bigmodel.cn/api/paas/v4",
  // anthropic / gemini 使用原生 SDK，不走 OpenAI-compatible
};

/**
 * 按 `LLM_PROVIDER` env 构造 Mastra `LanguageModel`（单租户模式，向后兼容）。
 *
 * @example
 *   // .env: LLM_PROVIDER=anthropic  LLM_MODEL=claude-opus-4-8  ANTHROPIC_API_KEY=sk-...
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

/**
 * 按用户配置动态构造 LLM（多租户模式）。
 *
 * @param userConfig 用户 LLM 配置（含解密后的 API key），为 null 时降级到 buildLLM()
 * @returns Mastra LanguageModel
 */
export function buildLLMForUser(userConfig: UserLLMConfig | null): LanguageModel {
  // 降级：用户未配置时使用系统默认
  if (!userConfig) {
    return buildLLM();
  }

  const model = userConfig.model || DEFAULT_MODELS[userConfig.provider as LLMProvider] || "gpt-4o";
  const baseUrl = userConfig.custom_base_url || PROVIDER_BASE_URLS[userConfig.provider];

  // 自定义端点：使用 OpenAI-compatible SDK
  if (userConfig.provider === "custom") {
    if (!userConfig.custom_base_url) {
      throw new Error("custom provider requires custom_base_url");
    }
    return createOpenAICompatible({
      name: userConfig.custom_provider_name || "custom",
      apiKey: userConfig.api_key,
      baseURL: userConfig.custom_base_url,
    })(model) as unknown as LanguageModel;
  }

  // 预设供应商
  switch (userConfig.provider) {
    case "deepseek":
      return createDeepSeek({
        apiKey: userConfig.api_key,
        baseURL: baseUrl,
      })(model) as unknown as LanguageModel;

    case "anthropic":
      return createAnthropic({
        apiKey: userConfig.api_key,
      })(model) as unknown as LanguageModel;

    case "openai":
      return createOpenAI({
        apiKey: userConfig.api_key,
        baseURL: baseUrl,
      })(model) as unknown as LanguageModel;

    case "gemini":
      return createGoogleGenerativeAI({
        apiKey: userConfig.api_key,
      })(model) as unknown as LanguageModel;

    case "kimi":
      return createOpenAICompatible({
        name: "kimi",
        apiKey: userConfig.api_key,
        baseURL: baseUrl || KIMI_BASE_URL,
      })(model) as unknown as LanguageModel;

    case "zhipu":
      return createOpenAICompatible({
        name: "zhipu",
        apiKey: userConfig.api_key,
        baseURL: baseUrl || ZHIPU_BASE_URL,
      })(model) as unknown as LanguageModel;

    default:
      throw new Error(`Unsupported provider: ${userConfig.provider}`);
  }
}

/**
 * 构建「用户感知」LanguageModel —— 按请求级 ALS 上下文选 LLM。
 *
 * Agent 实例只用这一个 model（构造时传入）；每次 mastra 调用 doGenerate/doStream
 * 时，proxy 检查 identity middleware 写入 ALS 的用户配置，有则用 buildLLMForUser()；
 * 单租户模式无用户配置时才使用系统级 buildLLM()。缓存按 ALS store 粒度（= 每请求一次 build），
 * 避免每次 property access 重建 model。
 *
 * **AUTH_ENABLED=true 时**：不允许 fallback 到系统级配置，必须要求用户配置 API Key。
 *
 * @returns 代理 LanguageModel
 */
export function buildUserAwareModel(): LanguageModel {
  const defaultModel = buildLLM();
  // 以 ALS store 为 key 缓存 per-request model；无 config 时返回 null（用 default）。
  const modelCache = new Map<UserLLMConfig | null | undefined, LanguageModel | null>();

  function resolveModel(): LanguageModel {
    const config = userLLMStore.getStore();
    console.log("[llm] resolveModel called, config:", config ? { id: config.id, provider: config.provider } : null, "AUTH_ENABLED:", AUTH_ENABLED);

    // AUTH_ENABLED=true 且无用户配置时，抛错阻断（不 fallback）
    if (AUTH_ENABLED && !config) {
      console.error("[llm] AUTH_ENABLED=true but no user config in ALS");
      throw new Error("AUTH_ENABLED=true 但用户未配置 LLM API Key");
    }

    if (!config) return defaultModel;
    const cached = modelCache.get(config);
    if (cached !== undefined) return cached ?? defaultModel;
    try {
      console.log("[llm] Building model for user config:", config.provider, config.model);
      const m = buildLLMForUser(config) as unknown as LanguageModel;
      modelCache.set(config, m);
      return m;
    } catch (err) {
      console.error("[llm] buildLLMForUser failed:", (err as Error).message);
      throw new Error(`用户 LLM 配置无效: ${(err as Error).message}`);
    }
  }

  // Proxy：拦截 doGenerate / doStream，其他属性透传 defaultModel。
  return new Proxy(defaultModel, {
    get(_target, prop, receiver) {
      if (prop === "doGenerate" || prop === "doStream") {
        const m = resolveModel();
        if (m === defaultModel) return Reflect.get(defaultModel, prop, receiver);
        return Reflect.get(m, prop, receiver);
      }
      return Reflect.get(defaultModel, prop, receiver);
    },
  }) as unknown as LanguageModel;
}
